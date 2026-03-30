"""
JSON 校验模块：对 LLM 生成的配方 JSON 进行分层校验。
顶层结构 -> plan 层 -> materials 局部 -> steps 层
输入：JSON
输出：错误/正确信息
"""
all_materials = {
    1: "环氧树脂",
    2: "固化剂",
    3: "稀释剂",
    4: "色浆"
}       # 原料库
all_target = ["safe","arm_start","arm_startforward","arm_startup","arm_stirup","arm_stir",
              "arm_endup","arm_end","pump_1","pump_2","pump_3","pump_4"]    # 位置库

all_amount = 400        #原料输出最大体积

# pump_vaild_state = ["arm_startup","pump_1","pump_2","pump_3","pump_4"]

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
        #print(f"顶层关键信息缺失，缺少的信息为{error}")
        error.append(f"顶层关键信息缺失，缺少的信息为{error}")
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
        has_cap = False  # 定义初始机械爪夹持状态
        plan_name = "方案" + str(plan.get('plan_id', '未命名'))    #读取当前plan编号
        # print(plan_name)
        stir_times = plan.get("stir_duration_seconds",0)    # 读取设定搅拌时间
        #err_cont = False        # 错误标志 已废弃
        has_printed = False     #设置打印初始状态
        amount_ml_all = 0       # 存储原料体积
        nothing_material = []   # 存储错误原料
        error_pumpid = []       # 大模型放置了错误原料的泵id
        pump_material = {}      # 按顺序存储LLM生成的原料，用于对比泵原料与LLM生成原料的泵是否相符
        materials_check_dict = {}   # 存放泵与输出体积，后续用于对比step

        materials = plan.get("materials", [])
        for materials_lists in materials:   # 遍历整个materials
            pump_id = materials_lists.get("pump_id")
            material = materials_lists.get("material")
            amount_ml = materials_lists.get("amount_ml", 0)

            amount_ml_all += amount_ml      # 计算总体积
            materials_check_dict[pump_id] = amount_ml # 存放与泵id对应体积
            pump_material[pump_id] = material # 按顺序存储LLM生成的原料

            if material not in all_materials.values():       # 记录没有的原材料
                nothing_material.append(material)
            if amount_ml <= 0:      # 检查输出的原料体积是否为负
                error.append(f"{plan_name}参数错误：泵 {pump_id} 的出料量必须大于0，当前为 {amount_ml}")

        for p_id, p_mat in pump_material.items():   # 对比各个泵的原料和生成的的是否相符
            if p_id in all_materials:
                real_mat = all_materials[p_id]
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
            error.append(f"{plan_name}方案中所加原料总数超过400ml，请修改")

        # === 第四层：对具体的执行步骤（steps）进行核对 ===
        steps = plan.get("steps", [])

        # 检查 steps 是否存在且是列表
        if not isinstance(steps, list):
            error.append(f"方案 {plan_name} 的 steps 格式错误")
            #err_cont = True，必须是列表")
            continue  # 跳过当前 plan 的后续检查

        current_position = "safe"
        pumped_pumps = set()
        # 开始遍历每一个步骤
        for step in steps:
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
                    elif target == "arm_end" and current_position != "arm_endup":
                        error.append(f"{plan_name}中步骤{step_id}路径危险：下降至出料口(arm_end)前，必须先到达过渡位(arm_endup)")
                    elif target == "arm_startup" and current_position != "arm_startforward":
                        error.append(f"{plan_name}中步骤{step_id}路径危险：抬起烧杯前，必须处于夹取位(arm_startforward)")

                    # 3. 构建一个虚拟机械臂
                    current_position = target   # 更新一下虚拟机器人位置


            # 检查 pump 步骤
            elif action == "pump":
                s_pump_id = step.get("pump_id")
                s_amount = step.get("amount_ml")

                # 1. 检查步骤中是否有空间冲突
                expected_position = f"pump_{s_pump_id}"
                if current_position != expected_position:
                    error.append(
                        f"{plan_name}中步骤{step_id}发生空间冲突！"
                        f"试图让泵{s_pump_id}出料，但前一步把机械臂停在了 '{current_position}'，没有移动到 '{expected_position}'"
                    )
                if has_cap == False:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步中机械臂没有夹持容器")

                # 2. 检查步骤里的泵号，在物料清单里有没有提到过
                if s_pump_id not in materials_check_dict:
                    err_cont = True
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步使用了物料清单中未定义的泵 {s_pump_id}")
                else:
                    # 3. 对比两边的数值是否完全一致
                    # materials_check_dict[s_pump_id] 是在 materials 里记下的标准值
                    expected_amount = materials_check_dict[s_pump_id]

                    if s_amount != expected_amount:
                        #err_cont = True
                        error.append(
                            f"指令冲突：第 {step_id} 步要求泵 {s_pump_id} 出料 {s_amount}ml，"
                            f"但物料清单里写的是 {expected_amount}ml"
                        )
                # 4. 记录该泵已经出过料了
                pumped_pumps.add(s_pump_id)



            #检查stir的时间与状态
            elif action == "stir":
                times = step.get("duration_seconds")
                if stir_times != times:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步搅拌时间{times}与设定的搅拌时间{stir_times}不符")
                elif times == 0:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步搅拌时间为【0】 ")
                elif has_cap:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步机械爪有容器")

            elif action == "print":
                # 检查打印动作
                has_printed = True
                content = step.get("content", "")
                if not content:
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步打印内容为空")
        # 检查打印动作是否执行
        if not has_printed:
            error.append(f"{plan_name}流程缺失：未执行标签打印(print)动作")

        # 检查是否所有清单里的泵都出过料了
        missing_pumps = set(materials_check_dict.keys()) - pumped_pumps
        if missing_pumps:
            error.append(f"{plan_name}逻辑不完整：物料清单中要求了泵 {missing_pumps}，但在步骤中未执行出料")

        # 检查末端行为状态
        if has_cap:
            error.append(f"{plan_name}结束状态危险：所有步骤执行完毕后，机械爪未松开(依然夹持容器)")
        if current_position != "safe":
            error.append(
                f"{plan_name}结束状态危险：所有步骤执行完毕后，机械臂未返回安全点(safe)，停留在 {current_position}")
    # 最终结果判断：只要 error 列表里有任何错误记录，就返回 False
    sys_cont = len(error) == 0
    return sys_cont, error




#if __name__ == "__main__":
    # bad_data = {
    #     "task_name": "格式测试",
    #     "requirement": "测试 plans 类型",
    #     "formula_reasoning": "因为我想测试",
    #     "plans": "我是一段文字，不是列表"
    # }
    # is_ok,err_list = validator_formula(bad_data)
    # if not is_ok:
    #     print(f"JSON不合格！！！！！！")
    #     print(f"错误信息为： {err_list}")
    # else:
    #     pass
    