import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox
from main_ui import Ui_MainWindow
from openai import OpenAI
from datetime import datetime
import json
import re
# import numpy
# import textwrap
import logging
from PyQt5.QtCore import QThread, pyqtSignal
from robot import RobotController
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
}

all_amount = 400        #原料输出最大体积

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
        print(f"顶层关键信息缺失，缺少的信息为{error}")
        return False, error

    plans = data.get("plans")       # 检查plans的数据格式
    if not isinstance(plans, list):
        error.append("plans的数据格式不正确，请重新生成")
        return False, error
    if len(plans) == 0:     # 检查plans的长度
        error.append("plans为空，请重新生成")
        return False, error

    # === 第三层:遍历plans检查中最大的遍历 ===
    for plan in plans:
        plan_name = plan.get('plan_name', '未命名')    #当前plan编号
        # print(plan_name)

        #err_cont = False        # 错误标志 已废弃

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

        # 开始遍历每一个步骤
        for step in steps:
            step_id = step.get("step_id")
            action = step.get("action")

            # 我们重点检查动作是 'pump' 的步骤
            if action == "pump":
                s_pump_id = step.get("pump_id")
                s_amount = step.get("amount_ml")

                # 1. 检查步骤里的泵号，在物料清单里有没有提到过
                if s_pump_id not in materials_check_dict:
                    err_cont = True
                    error.append(f"{plan_name}中步骤错误：第 {step_id} 步使用了物料清单中未定义的泵 {s_pump_id}")
                else:
                    # 2. 对比两边的数值是否完全一致
                    # materials_check_dict[s_pump_id] 是刚才在 materials 里记下的“标准值”
                    expected_amount = materials_check_dict[s_pump_id]

                    if s_amount != expected_amount:
                        #err_cont = True
                        error.append(
                            f"指令冲突：第 {step_id} 步要求泵 {s_pump_id} 出料 {s_amount}ml，"
                            f"但物料清单里写的是 {expected_amount}ml"
                        )

    # 最终结果判断：只要 error 列表里有任何错误记录，就返回 False
    is_ok = len(error) == 0
    return is_ok, error




if __name__ == "__main__":
    bad_data = {
        "task_name": "格式测试",
        "requirement": "测试 plans 类型",
        "formula_reasoning": "因为我想测试",
        "plans": "我是一段文字，不是列表"
    }
    is_ok,err_list = validator_formula(bad_data)
    if not is_ok:
        print(f"JSON不合格！！！！！！")
        print(f"错误信息为： {err_list}")
    else:
        pass
    