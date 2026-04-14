
"""
JSON 校验模块：对 LLM 生成的配方 JSON 进行分层校验。
顶层结构 -> plan 层 -> materials 局部 -> steps 层
输入：JSON
输出：错误/正确信息
"""
MATERIAL_CONFIG = {
    1: {"name": "丙烯酸乳液", "ratio_min": 0.4, "ratio_max": 0.7, "solid_content": 0.37, "must_be_last": True, "need_stir_after": False},
    2: {"name": "氟化丙烯酸消光剂+消泡剂", "ratio_min": 0.01, "ratio_max": 0.10, "solid_content": 1.0, "must_be_last": False, "need_stir_after": True},
    3: {"name": "炭黑分散液", "ratio_min": 0, "ratio_max": 0.5, "solid_content": 0.10, "must_be_last": False, "need_stir_after": False},
    4: {"name": "去离子水", "ratio_min": 0, "ratio_max": 0.3, "solid_content": 0.0, "must_be_last": False, "need_stir_after": False},
}       # 原料库配置
all_target = ["safe","arm_start","arm_startforward","arm_startup","arm_stirup","arm_stir",
              "arm_endup","arm_end","pump_1","pump_2","pump_3","pump_4"]    # 位置库
required_plan_keys = {"plan_id", "materials", "steps", "plan_name", "plan_reasoning"}  # 必要动作字段
all_amount = 100        #原料输出最大体积

# pump_vaild_state = ["arm_startup","pump_1","pump_2","pump_3","pump_4"]

def check_ratio(materials, plan_name, total_ml):
    errors = []
    if total_ml is None or total_ml <= 0:
        return errors
    for m in materials:
        pid = m.get("pump_id")
        total = m.get("total_amount_ml", m.get("amount_ml", 0))
        if pid in MATERIAL_CONFIG:
            name = MATERIAL_CONFIG[pid]["name"]
            ratio = total / total_ml
            min_r = MATERIAL_CONFIG[pid]["ratio_min"]
            max_r = MATERIAL_CONFIG[pid]["ratio_max"]
            if ratio < min_r or ratio > max_r:
                errors.append(
                    f"{plan_name}泵{pid}原料比例({ratio:.2f})不在允许区间[{min_r:.2f},{max_r:.2f}]之间，原料为{name}"
                )
        else:
            # 未在配置表中的泵在前面的校验中已报错，此处仅作为保护
            pass
    return errors

def check_add_order(steps, pump_order_map, plan_name):
    errors = []
    # 提取实际执行顺序的泵号
    actual_order = []
    for s in steps:
        if s.get("action") == "pump":
            actual_order.append(s.get("pump_id"))
    # 将实际执行的 add_order 转换为顺序数组（若缺失则忽略该泵）
    actual_add_orders = []
    for pid in actual_order:
        if pid in pump_order_map:
            actual_add_orders.append(pump_order_map[pid])
    # 检查是否严格递增，若不是则报错
    for i in range(len(actual_add_orders) - 1):
        if actual_add_orders[i] > actual_add_orders[i+1]:
            errors.append(
                f"{plan_name}泵出料顺序与 add_order 不一致：泵序 {actual_order[i]} 的 add_order={actual_add_orders[i]} 应早于泵序 {actual_order[i+1]} 的 add_order={actual_add_orders[i+1]}"
            )
    return errors

def check_must_be_last(steps, pump_order_map, plan_name):
    errors = []
    must_last_pids = [pid for pid, info in MATERIAL_CONFIG.items() if info.get("must_be_last")]
    if not must_last_pids:
        return errors
    # 计算每个泵在步骤中的最后一次出现位置
    last_pos = {}
    for idx, s in enumerate(steps):
        if s.get("action") == "pump":
            pid = s.get("pump_id")
            last_pos[pid] = idx
    for pid in must_last_pids:
        if pid in last_pos:
            last_idx = last_pos[pid]
            # 检查是否还有其他泵在其后出料
            for other_pid, idx in last_pos.items():
                if other_pid != pid and idx > last_idx:
                    name = MATERIAL_CONFIG[pid]["name"]
                    other_name = MATERIAL_CONFIG[other_pid]["name"]
                    errors.append(
                        f"{plan_name}必须将原料{name}(泵{pid})置于最后，但泵{other_pid}（{other_name}）在其后出料"
                    )
                    break
    return errors

def validator_formula(data):
    # 字典格式检查
    error = []      # 作为总错误输出信息
    if not isinstance(data, dict):  # 检查数据格式是否正确
        error.append("数据格式不正确，请重新生成")
        return False, error

    # 最顶层的json信息检查
    request_key = {"task_name", "requirement", "formula_reasoning", "plans"}
    # data.keys 读取json的抽屉名字
    missing_keys = request_key - data.keys()    # 二者相减，得到没有生成的抽屉类型
    if missing_keys:
        error_msg = f"顶层关键信息缺失: {', '.join(missing_keys)}"
        error.append(error_msg)
        # print(f"顶层关键信息缺失，缺少的信息为{error}")
        return False, error

    plans = data.get("plans")       # 检查plans的数据格式
    if not isinstance(plans, list):
        error.append("plans的数据格式不正确，请重新生成")
        return False, error
    if len(plans) == 0:     # 检查plans的长度
        error.append("plans为空，请重新生成")
        return False, error

    # === 第三层:遍历plans，检查中最大的遍历 ===
    for plan in plans:
        missing = required_plan_keys - plan.keys()
        if missing:
            error.append(f"方案缺失必要字段: {missing}")
            continue
        has_cap = False  # 定义初始机械爪夹持状态
        plan_name = "方案" + str(plan.get('plan_id', '未命名'))    #读取当前plan编号
        # print(plan_name)
        # stir_times = plan.get("stir_duration_seconds",0)    # 读取设定搅拌时间 已废弃
        #err_cont = False        # 错误标志 已废弃
        #has_printed = False     #设置打印初始状态 打印已经废弃
        amount_ml_all = 0       # 存储原料体积
        nothing_material = []   # 存储错误原料
        error_pumpid = []       # 大模型放置了错误原料的泵id
        pump_material = {}      # 按顺序存储LLM生成的原料，用于对比泵原料与LLM生成原料的泵是否相符
        materials_check_dict = {}   # 存放泵与输出体积，后续用于对比step
        pump_order_map = {}         # 记录 pump_id 对应的 add_order（来自材料输出）

        materials = plan.get("materials", [])
        if not isinstance(materials, list):     # 判断原材料是否为空或者是否为list
            error.append(f"{plan_name}的materials格式错误")
            continue
        for materials_lists in materials:   # 遍历整个materials
            pump_id = materials_lists.get("pump_id")
            material = materials_lists.get("material")
            total_amount_ml = materials_lists.get("total_amount_ml", 0)
            add_order = materials_lists.get("add_order", None)

            amount_ml_all += total_amount_ml      # 计算总体积
            materials_check_dict[pump_id] = total_amount_ml # 存放与泵id对应体积
            pump_material[pump_id] = material # 按顺序存储LLM生成的原料
            if add_order is not None:
                pump_order_map[pump_id] = add_order  # 记录 add_order

            # 校验原材料是否在配置表中
            if material not in [v["name"] for v in MATERIAL_CONFIG.values()]:       # 记录没有的原材料
                nothing_material.append(material)
            if total_amount_ml <= 0:      # 检查输出的原料量是否为负
                error.append(f"{plan_name}参数错误：泵 {pump_id} 的出料量必须大于0，当前为 {total_amount_ml}")

        for p_id, p_mat in pump_material.items():   # 对比各个泵的原料和生成的的是否相符
            if p_id in MATERIAL_CONFIG:
                real_mat = MATERIAL_CONFIG[p_id]["name"]
                if p_mat != real_mat:
                    #err_cont = True
                    error_pumpid.append(p_id)  # 记录出错的泵号
                    error.append(f"警告：{plan_name}方案中泵{p_id}原料({p_mat})与物理系统实际原料({real_mat})不符！")
            else:
                #err_cont = True
                error.append(f"错误：{plan_name}使用了不存在的泵号 {p_id} (系统仅支持1-4号泵)")

        if nothing_material:
            #err_cont = True        # 错误标志信号
            error.append(f"{plan_name}方案中所需原料{nothing_material}，原料库中不存在")
        if amount_ml_all > all_amount:
            #err_cont = True
            error.append(f"{plan_name}方案中所加原料总数超过100ml，请修改")



        # === 第四层：对具体的执行步骤（steps）进行核对 ===
        steps = plan.get("steps", [])

        # 检查 steps 是否存在且是列表
        if len(steps) == 0:
            error.append(f"{plan_name}的steps为空，没有任何执行步骤")
        if not isinstance(steps, list):
            error.append(f"方案 {plan_name} 的 steps 格式错误")
            #err_cont = True，必须是列表")
            continue  # 跳过当前 plan 的后续检查
        if len(pump_order_map) != len(materials_check_dict):        # 校验原材料加入顺序是否写了
            error.append(f"{plan_name}部分原料缺少add_order字段，无法校验加料顺序")
        # 额外校验：比例、加入顺序与 must_be_last
        total_ml_all = amount_ml_all
        ratio_errors = check_ratio(materials, plan_name, total_ml_all)
        error.extend(ratio_errors)
        order_errors = check_add_order(steps, pump_order_map, plan_name)
        error.extend(order_errors)
        must_last_errors = check_must_be_last(steps, pump_order_map, plan_name)
        error.extend(must_last_errors)

        current_position = "safe"
        pumped_pumps = set()
        accumulated_pump_amounts = {k: 0 for k in materials_check_dict.keys()}  #初始化泵量计数器
        # 开始遍历每一个步骤
        for idx, step in enumerate(steps):
            step_id = step.get("step_id")
            action = step.get("action")
            # 检查机械爪的夹持状态
            if action == "grip":
                state = step.get("state","")
                if len(state) == 0:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步中，机械爪抓取指令为空")
                elif state == "close":
                    if has_cap == False:
                        has_cap = True
                    else:
                        error.append(f"{plan_name}中步骤错误：第 {step_id} 步中，机械爪已经抓取了杯子")
                elif state == "open":
                    if has_cap == True:
                        has_cap = False
                    else:
                        error.append(f"{plan_name}中步骤错误：第 {step_id} 步中，机械爪手上没有杯子")
                else:
                    # 对不可预见状态拦截
                    error.append(f"{plan_name}步骤{step_id}参数错误：未知的夹爪状态 '{state}'")

            # 检查 move 步骤
            elif action == "move":
                # 1. 先检查target是否为设置的位置
                target = step.get("target","")
                if len(target) == 0:
                    error.append(f"{plan_name}中的步骤{step_id}中出现了空移动目标")
                    #print(f"{plan_name}中的步骤{step_id}出现了空位置")
                elif target not in all_target:
                    error.append(f"{plan_name}中的步骤{step_id}中出现了未定义移动目标")
                    #print(f"{plan_name}中的步骤{step_id}中出现了未定义移动目标")
                # 2. 防碰撞路径检查
                else:
                    if target == "arm_stir" and current_position != "arm_stirup":
                        error.append(f"{plan_name}中步骤{step_id}路径危险：下降至搅拌台(arm_stir)前，必须先到达过渡位(arm_stirup)")
                    if target == "arm_end" and current_position != "arm_endup":
                        error.append(f"{plan_name}中步骤{step_id}路径危险：下降至出料口(arm_end)前，必须先到达过渡位(arm_endup)")
                    if target == "arm_startup" and current_position != "arm_startforward":
                        error.append(f"{plan_name}中步骤{step_id}路径危险：抬起烧杯前，必须处于夹取位(arm_startforward)")
                    if current_position == "arm_stir" and target != "arm_stirup":
                        error.append(f"{plan_name}步骤{step_id}路径危险：从搅拌台离开必须先回过渡位(arm_stirup)")
                    if current_position == "arm_end" and target != "arm_endup":
                        error.append(f"{plan_name}步骤{step_id}路径危险：从出料口离开必须先回过渡位(arm_endup)")
                    # 3. 构建一个虚拟机械臂
                    current_position = target   # 更新一下虚拟机器人位置


            # 检查 pump 步骤
            elif action == "pump":
                s_pump_id = step.get("pump_id")
                s_amount = step.get("amount_ml",0)

                # 1. 检查步骤中是否有空间冲突
                expected_position = f"pump_{s_pump_id}"
                if current_position != expected_position:
                    error.append(
                        f"{plan_name}中步骤{step_id}发生空间冲突！"
                        f"试图让泵{s_pump_id}出料，但前一步把机械臂停在了 '{current_position}'，没有移动到 '{expected_position}'"
                    )
                if has_cap == False:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步中机械臂没有夹持容器")

                    # 2. 记账
                    # 如果大模型用了一个物料清单里根本没有的泵，报错
                if s_pump_id not in accumulated_pump_amounts:
                    error.append(f"{plan_name}步骤{step_id}越权调用：使用了物料清单中未规划的泵 {s_pump_id}")
                else:
                    # 只要泵抽出的量正常（大于0）
                    if s_amount <= 0:
                        error.append(f"{plan_name}步骤{step_id}参数错误：单次泵出量必须大于0")

                        # 把这次加的量，存进账本里
                    accumulated_pump_amounts[s_pump_id] += s_amount

                # 额外校验：若该泵对应的原料需要搅拌后再出料，则确保下一步之前有 stir 动作
                if s_pump_id in MATERIAL_CONFIG and MATERIAL_CONFIG[s_pump_id].get("need_stir_after", False):
                    found_stir = False
                    for future in steps[idx+1:]:
                        f_action = future.get("action")
                        if f_action == "pump":
                            break
                        if f_action == "stir":
                            found_stir = True
                            break
                    if not found_stir:
                        error.append(f"{plan_name}中步骤{step_id}未在泵出料后进行搅拌（泵{s_pump_id}需要在下一次出料前搅拌）")



            #检查stir的时间与状态
            elif action == "stir":
                duration = step.get("duration_seconds",0)
                speed = step.get("speed_rpm",0)
                if duration <= 0:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步搅拌时间为【0】 ")
                if speed <= 0:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步搅拌速度为【0】 ")
                # if stir_times != duration:        # 已废弃
                #     error.append(f"{plan_name}中步骤错误：第 {step_id} 步搅拌时间{duration}与设定的搅拌时间{stir_times}不符")
                if has_cap:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步机械爪有容器")
                if current_position != "arm_stir":
                    error.append(f"{plan_name}中步骤错误：第{step_id} 步位置错误：启动搅拌器时，机械臂理论上应停留在搅拌台(arm_stir)位置")
            else:
                error.append(f"{plan_name}步骤{step_id}：未知的动作类型 '{action}'")

        # 检查末端行为状态
        if has_cap:
            error.append(f"{plan_name}结束状态危险：所有步骤执行完毕后，机械爪未松开(依然夹持容器)")
        if current_position != "safe":
            error.append(
                f"{plan_name}结束状态危险：所有步骤执行完毕后，机械臂未返回安全点(safe)，停留在 {current_position}")
        # 算泵输出总账
        for p_id, expected_total in materials_check_dict.items():
            # 获取账本里，这个泵总共抽了多少料
            actual_total = accumulated_pump_amounts.get(p_id, 0)

            # 最后时刻，对比大模型规划的总抽料量，和配方要求的总量是否一致
            if actual_total != expected_total:
                error.append(
                    f"{plan_name}物料不守恒：清单要求泵{p_id}总计输出 {expected_total}ml，但在实际执行序列中总计输出了 {actual_total}ml")
    # 最终结果判断：只要 error 列表里有任何错误记录，就返回 False
    sys_cont = len(error) == 0
    return sys_cont, error
    
