import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox
from main_ui import Ui_MainWindow
from openai import OpenAI
from datetime import datetime
import json
import re
import os
import logging
from PyQt5.QtCore import QThread, pyqtSignal, QObject
from robot import RobotController
from vaildator import validator_formula, MATERIAL_CONFIG
import serial.tools.list_ports
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtCore import Qt

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
API_KEY = os.getenv("DEEPSEEK_API_KEY")
client = OpenAI(
     api_key=API_KEY,
     base_url="https://api.deepseek.com"
)

sim = True
# 原料库
materials_desc = "\n".join([
    f"    泵{k}: \"{v['name']}\" (占总量比例 {int(v['ratio_min']*100)}%~{int(v['ratio_max']*100)}%)"
    for k, v in MATERIAL_CONFIG.items()
])
sys_text_template = """
你是一个专业的涂层配制专家，根据用户需求推理配方并输出JSON。

点位定义    点位名说明
safe    机械臂初始安全位置，所有动作的起点与回程中转点
arm_start   烧杯正后方等待位
arm_startforward    前移至烧杯夹取位（与烧杯对齐）
arm_startup 夹取烧杯后垂直抬起位
pump_1 ~ pump_4 各泵出料等待位（沿圆弧排布，对应J2不同角度）
arm_stirup  搅拌台正上方过渡位
arm_stir    搅拌台放置位
arm_endup   出料口正上方过渡位
arm_end 出料口放置位

原子动作指令集（Action Space）：
你可以组合以下动作来规划复杂的配制任务。

move: 移动到指定点位。参数: target (点位名)

grip: 控制夹爪。参数: state ("close" 或 "open")

pump: 控制蠕动泵出料。参数: pump_id (1-4), amount_ml (数字)

stir: 启动磁力搅拌器。参数: duration_seconds (数字), speed_rpm (数字)


执行流程约束：
① 必须以 safe 移动起手，依次经过 arm_start -> arm_startforward -> 夹爪 close -> arm_startup 抓取烧杯。
② 动态加料与搅拌：你可以自由规划加料顺序。可以去 pump_x 接料，也可以中途将烧杯送到 arm_stir 放下并执行 stir 动作（需设定时间和转速）。如果需要分段加料，搅拌完成后需重新夹取烧杯，抬起至 arm_stirup，再移动到新的 pump_x 接料。接料和搅拌可根据配方逻辑交替进行。
③ 配制完成后出料：将烧杯移至 arm_end 放下，夹爪 open。
④ 归位：机械臂退回 safe 结束任务。

你必须严格按照以下JSON格式输出，不要输出任何JSON以外的内容，不要加markdown代码块，不要加任何解释：

{
"task_name": "任务名称 要详细简短",
"requirement": "用户需求原文",
"formula_reasoning": "整体选料依据 如果你校验后才通过 此处必须加上选料依据 不要写系统校验要求 按原始要求写",
"plans": [
{
"plan_id": 1,
"plan_name": "方案A：xxx",
"plan_reasoning": "该方案的具体理由",
"materials": [
{"pump_id": 1, "material": "材料名1", "total_amount_ml": 数量, "add_order": 1},
{"pump_id": 2, "material": "材料名2", "total_amount_ml": 数量, "add_order": 2}
],
"steps": [
{"step_id": 1,  "action": "move",  "target": "safe"},
{"step_id": 2,  "action": "move",  "target": "arm_start"},
...
{"step_id": N,  "action": "pump",  "pump_id": 1, "amount_ml": 50},
...
{"step_id": M,  "action": "stir",  "duration_seconds": 数字, "speed_rpm": 数字},
...
{"step_id": K,  "action": "pump",  "pump_id": 2, "amount_ml": 30},
...     
{"step_id": K,  "action": "move",  "target": "arm_endup"},
{"step_id": K,  "action": "move",  "target": "arm_end"},
...
]
}
]
}

以下是关键动作序列的示例片段，你在生成steps时必须严格遵照这个结构：

【搅拌序列示例】
{"step_id": N,   "action": "move",  "target": "arm_stirup"},
{"step_id": N+1, "action": "move",  "target": "arm_stir"},
{"step_id": N+2, "action": "grip",  "state": "open"},
{"step_id": N+3, "action": "stir",  "duration_seconds": 120, "speed_rpm": 600},
{"step_id": N+4, "action": "grip",  "state": "close"},
{"step_id": N+5, "action": "move",  "target": "arm_stirup"},

【出料口序列示例】
{"step_id": M,   "action": "move",  "target": "arm_endup"},
{"step_id": M+1, "action": "move",  "target": "arm_end"},
{"step_id": M+2, "action": "grip",  "state": "open"},
{"step_id": M+3, "action": "move",  "target": "arm_endup"},
{"step_id": M+4, "action": "move",  "target": "safe"},

注意：N和M是实际步骤中的编号占位符，生成时替换为连续的实际编号。
除duration_seconds和speed_rpm的具体数值外，序列中的action和target不允许有任何偏差。

注意事项：

只使用pump_id 1到4，最多用4种原料

生成多个方案，每个方案配比、配方可以不同，方案数量由你决定，但是不要超过五个

方案内各 pump_id 在 steps 中分配的 amount_ml 总和，必须与 materials 数组中对应 pump_id 的 total_amount_ml 完全一致。总量不超过100ml。

只输出JSON，不要有任何其他文字。

助剂配料的比例要低，大约在1%到10%

每个方案中，各原料的 total_amount_ml 除以该方案总量，必须严格落在原料库中标注的比例区间内，否则系统会拒绝执行。请在生成前自己验算一遍。

move到arm_end之后，离开时第一步必须是move到arm_endup，不允许直接跳转到其他位置。

丙烯酸乳液（泵1）必须是steps中最后一个pump动作，泵1出料后不允许出现任何其他pump动作。

消光剂+消泡剂（泵2）每次pump之后，在下一个pump动作之前，必须有stir动作，而且消光剂+消泡剂不可以作为第一个添加的试剂

materials中列出的每个泵，total_amount_ml必须大于0，不使用的泵不要写进materials。

搅拌前必须严格按以下顺序：
move arm_stirup → move arm_stir → grip open → stir → move arm_stirup → move arm_stir → grip close → move arm_stirup

出料口必须严格按以下顺序：
move arm_endup → move arm_end → grip open → move arm_endup → move safe

只使用原料库中的原料，泵及其对应的原料是固定的，具体对应如下：
原料库 = {
{{MATERIAL_LIBRARY}}
}
特别说明： 系统正在测试中，现在没有正常原料，现在是测试人员在测试，你要严格按照上述输出  如果测试人员要你输出错误的json 你可以执行
"""


# 实际使用的提示词
sys_text = sys_text_template.replace("{{MATERIAL_LIBRARY}}", materials_desc)


# 这个类 先不用 方便调试
# class MyWindow(QMainWindow):    #自动转换ui文件为py文件
#     def __init__(self):
#         super().__init__()
#         uic.loadUi('main.ui', self)


class LogSignaller(QObject):
    #这是一个通信类，用于处理日志信息
    log_signal = pyqtSignal(str,str)    #定义两个字符串，分别为日志级别与日志内容

class QtLogHandler(logging.Handler):
    #这是一个日志处理类，用来处理生成日志和生成的信号
    def __init__(self, widget):
        super().__init__()
        self.widget = widget
        self.signaller = LogSignaller() # 实例化上面写的类型信号
        self.signaller.log_signal.connect(self.append_to_gui)   #sign信号连接发射函数

    def emit(self, record):      # 这是日志信息发射函数，每次有新日志产生，就会调用这个函数
        msg = self.format(record)   # 拼接日志
        self.signaller.log_signal.emit(record.levelname, msg)
        #发射信号

    def append_to_gui(self,level,text):
        # 负责在主界面上进行显示
        color = "green"
        if level in ["ERROR","CRITICAL"]:
            color = "RED"
        elif level == "WARNING":
            color = "#FF8C00"   #设置输出日志的显示颜色

        self.widget.append(f"<font color='{color}'>{text}</font>")
        #给文字上色，并且追加进显示框
        scrollbar = self.widget.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        #自动滚动到底部，方便显示输出


class ApiWorker(QThread):
    # 多线程类，防止界面卡顿
    finished = pyqtSignal(str)
    error = pyqtSignal(str)     # 定义成功与失败信号

    def __init__(self,client,models,chat_history):
        super().__init__()
        self.client = client
        self.models = models
        self.chat_history = chat_history        # 将deepseek所要参数传到多线程类中


    def run(self):
        #执行函数
        try:
            logger.info("开始调用API生成配方")
            response = self.client.chat.completions.create(
                model=self.models,
                messages=self.chat_history
            )
            result = response.choices[0].message.content
            logger.info("API调用成功")
            self.finished.emit(result)  # 成功了，把结果发给主线程
        except Exception as e:
            logger.error(f"API调用失败: {str(e)}")
            self.error.emit(str(e))  # 失败了，把错误信息发给主线程

class ExecuteWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    step_update = pyqtSignal(str)  # 每执行一步更新界面
    led_update = pyqtSignal(str, str)       #led名字，状态

    def __init__(self, robot, plans):
        super().__init__()
        self.robot = robot
        self.plans = plans

    def run(self):
        try:
            logger.info(f"开始执行方案，共 {len(self.plans)} 个方案")
            self.led_update.emit('led_system', 'running')       #设置系统灯光为：运行中
            for plan in self.plans:
                logger.info(f"开始执行方案：{plan['plan_name']}")
                self.step_update.emit(f"开始执行方案：{plan['plan_name']}")
                for step in plan['steps']:
                    action = step['action']
                    if action == 'move':
                        self.led_update.emit('led_arm', 'running')
                    elif action == 'pump':
                        self.led_update.emit('led_arm', 'idle')
                        self.led_update.emit(f"led_pump{step['pump_id']}", 'running')
                    elif action == 'stir':
                        self.led_update.emit('led_arm', 'idle')
                        self.led_update.emit('led_stir', 'running')
                    elif action == 'grip':
                        self.led_update.emit('led_arm', 'running')

                    self.robot.execute_step(step)       # 阻塞线程，等待机械臂完成动作

                    # 执行完更新灯为空闲
                    if action == 'pump':
                        self.led_update.emit(f"led_pump{step['pump_id']}", 'idle')
                    elif action == 'stir':
                        self.led_update.emit('led_stir', 'idle')

            self.led_update.emit('led_system', 'ok')
            self.led_update.emit('led_arm', 'idle')
            logger.info("全部方案执行完成")
            self.finished.emit("全部方案执行完成")
        except Exception as e:
            logger.error(f"执行过程中发生错误: {str(e)}")
            self.led_update.emit('led_system', 'idle')
            self.error.emit(str(e))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        logger.info("应用程序启动")
        self.setStyleSheet("QPushButton { border-radius: 4px; padding: 5px; }")
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)  # 把界面加载进来
        # 加圆角、边框和鼠标放置高亮
        self.setStyleSheet("""          
                    QPushButton {
                        border: 1px solid #888888; 
                        border-radius: 8px;
                        padding: 4px;
                    }
                    QLineEdit, QTextEdit, QComboBox, QPlainTextEdit {
                        border: 1px solid #555555;
                        border-radius: 6px;
                    }
                    QPushButton:hover {
                        border: 1px solid #AAAAAA;
                        background-color: #444444;
                    }
                """)

        self.auto_correct_count = 0     #定义自动纠错计数器

        # 生成日志和配方文件夹
        self.log_dir = "运行日志"
        self.recipe_dir = "保存的配方"
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.recipe_dir, exist_ok=True)     # 写文件
        self.refresh_ports()    # 刷新串口信息
        #日志处理区
        ## 定义日志格式:时间 - 模块名 - 等级 - 消息内容
        log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ## 将日志写在txt文件中
        log_file_path = f"{self.log_dir}/{datetime.now().strftime('%Y-%m-%d')}_log.txt"
        file_h = logging.FileHandler(log_file_path, encoding='utf-8')
        file_h.setFormatter(log_format)  # 带上标准格式
        ## 把日志发给 UI
        gui_h = QtLogHandler(self.ui.runing_text)
        gui_h.setFormatter(log_format)  # 带上标准格式
        # 获取“总记录器”（Root Logger）
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)  # 设置拦截门槛，INFO 及以上的才管
        root_logger.addHandler(file_h)  # 往文件里写
        root_logger.addHandler(gui_h)  # 往界面上发

        logger.info("日志系统就绪，系统启动中")


        for led in ['led_system', 'led_arm', 'led_pump1', 'led_pump2', 'led_pump3', 'led_pump4', 'led_stir']:
            self.set_led(led, 'idle')       #初始化状态显示灯

        self.models = "deepseek-reasoner"
        self.models_text = "推理模型"       #切换模型所用的中间变量
        self.robot = RobotController('192.168.201.1', simulate=sim)  # 创建机器人运行对象
        self.ui.label_model.setText("当前模型:" + self.models_text)
        logger.info("系统初始化完成")
        # 把按钮的点击信号连接到函数
        self.ui.start_button.clicked.connect(self.on_start)
        self.ui.model_button.clicked.connect(self.trans_model)
        self.ui.clear_button.clicked.connect(self.clear_history)
        self.ui.execute_button.clicked.connect(self.on_execute)
        self.ui.save_button.clicked.connect(self.on_save_clicked)
        # 选串口的信号连接到函数
        self.ui.combo_port_pump.currentTextChanged.connect(self.on_port_changed)
        self.ui.combo_port_stir.currentTextChanged.connect(self.on_port_changed)
        # 用当前选中的值初始化一次
        self.on_port_changed()
        # self.ui.stop_button.clicked.connect(self.on_stop)
        # self.ui.reset_button.clicked.connect(self.on_reset)           #暂停和复位按钮暂时不上


        self.chat_history = [
            {"role": "system", "content": sys_text}  # 提示词
        ]

    def refresh_ports(self):  # 串口初始化，寻找串口
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.ui.combo_port_pump.clear()
        self.ui.combo_port_stir.clear()
        if ports:
            self.ui.combo_port_pump.addItems(ports)
            self.ui.combo_port_stir.addItems(ports)
        else:
            self.ui.combo_port_pump.addItem("无可用串口")
            self.ui.combo_port_stir.addItem("无可用串口")

    def on_port_changed(self):
        # 执行中不允许切换
        if hasattr(self, 'execute_worker') and self.execute_worker.isRunning():
            return
        port_pump = self.ui.combo_port_pump.currentText()
        port_stir = self.ui.combo_port_stir.currentText()
        if "无可用串口" in port_pump or "无可用串口" in port_stir:
            return
        self.robot.start(port_pump, port_stir)

    def closeEvent(self, event):        # 停止函数
        # 先停线程
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
        if hasattr(self, 'execute_worker') and self.execute_worker.isRunning():
            self.execute_worker.terminate()
            self.execute_worker.wait()
        # 停硬件
        self._shutdown_hardware()

        logger.info("程序正常退出")
        event.accept()

    def _shutdown_hardware(self):
        try:
            self.robot.shutdown()

        except Exception as e:
            logger.error(f"硬件关机异常: {e}")

    def on_start(self):
        user_input = self.ui.lineEdit.text()  # 读取输入框内容
        if(user_input == ""):
            logger.warning("用户未输入任何信息")
            QMessageBox.warning(self,"警告","用户未输入任何信息")
            #self.ui.json_text.setText("未检测到信息输入")
        else:
            self.auto_correct_count = 0     # 开始时计数器清零

            logger.info(f"用户输入: {user_input}")
            self.ui.start_button.setEnabled(False)          # 禁用按钮，防止重复点击
            self.ui.json_text.setText("正在生成配方，请稍候...")
            self.chat_history.append({"role": "user", "content": user_input})     #历史消息
            self.worker = ApiWorker(client, self.models, self.chat_history)     #调用子线程，传参
            # 把信号连接到处理函数
            self.worker.finished.connect(self.on_api_success)
            self.worker.error.connect(self.on_api_error)
            # 启动子线程
            self.worker.start()

    def on_api_success(self, result):
        # API调用成功函数
        result = re.sub(r'```json|```', '', result).strip()  # 清洗markdown标记

        # 解析并调用validator_formula安检
        try:
            data = json.loads(result)
            data["created_at"] = datetime.now().isoformat()     # 得到时间信息，放在json里面

            # 从检验函数中拿到错误信息
            is_ok, err_list = validator_formula(data)

        except json.JSONDecodeError as e:   # 从系统中得到json解析失败的原因
            is_ok = False
            err_list = [f"JSON格式解析失败，请严格输出合法的JSON格式。具体错误：{str(e)}"]
        except Exception as e:
            is_ok = False
            err_list = [f"系统发生未知解析异常：{str(e)}"]

        # 安检未通过，触发纠错或熔断
        if not is_ok:
            self.auto_correct_count += 1
            error_msg = "\n".join(err_list)  # 将错误列表拼接成字符串

            # 1. 触发熔断：错误超过3次，停机报警
            if self.auto_correct_count >= 3:
                # 发起警报
                logger.error(f"[最高警报] 模型连续 {self.auto_correct_count} 次生成存在物理冲突的配方！")
                logger.error("自动纠错已失效。为保护硬件，系统已强制停止运行。")
                for err in err_list:
                    logger.error(f"拦截详情：{err}")

                QMessageBox.critical(
                    self,
                    "最高警报 - 阻断执行",
                    f"模型连续 {self.auto_correct_count} 次生成存在物理冲突的配方！\n"
                    f"为保护硬件，系统已强制停止。\n\n"
                    f"最后一次拦截的致命错误：\n{error_msg}"
                )
                self.auto_correct_count = 0  # 报警后清零计数器
                self.ui.start_button.setEnabled(True)
                return

            # 2. 触发纠错：调用logger.warning
            logger.warning(f"[系统消息] 检测到逻辑冲突，第 {self.auto_correct_count} 次自动纠错...")
            for err in err_list:
                logger.warning(f"检测到物理冲突：{err}")

            # 在左侧的 JSON 框里提示正在等待
            self.ui.json_text.setText(
                f"配方存在逻辑冲突，正在请求 AI 进行第 {self.auto_correct_count} 次深度纠错，请稍候...\n\n错误详情：\n{error_msg}")

            # 把查出的错，喂给AI
            self.chat_history.append({"role": "assistant", "content": result})  # 把它刚才写错的 JSON 存进去
            correction_prompt = (
                f"你刚才生成的配方未能通过系统物理安全校验，包含以下致命错误：\n"
                f"{error_msg}\n\n"
                f"请作为专业的控制算法专家，严格根据上述报错逐一修改配方，修正空间冲突与步骤遗漏，"
                f"并重新输出一个完整的、可直接执行的JSON。切记只输出JSON格式代码，不要包含任何解释文字。"
            )
            self.chat_history.append({"role": "user", "content": correction_prompt})  # 运行

            # 重新启动多线程发起API请求
            self.worker = ApiWorker(client, self.models, self.chat_history)
            self.worker.finished.connect(self.on_api_success)
            self.worker.error.connect(self.on_api_error)
            self.worker.start()
            return

        # 3. 安检通过
        self.auto_correct_count = 0  # 计数器复位
        self.chat_history.append({"role": "assistant", "content": result})

        # 调用logger.info
        logger.info("[系统消息] 配方生成完毕且通过安全校验！")

        data_show = json.dumps(data, ensure_ascii=False, indent=2)
        self.ui.json_text.setText(data_show)
        self.ui.start_button.setEnabled(True)

    def on_api_error(self,error_msg):
        # API调用失败
        self.ui.json_text.setText("调用失败：" + error_msg)
        self.ui.start_button.setEnabled(True)   # 开启按钮

    def trans_model(self):
        # 转换模型的函数
        if self.models == "deepseek-chat" :
            self.models = "deepseek-reasoner"
            self.models_text = "推理模型"
        elif self.models == "deepseek-reasoner" :
            self.models = "deepseek-chat"
            self.models_text = "普通模型"
        self.ui.label_model.setText("当前模型:"+self.models_text)

    def clear_history(self):
        #对话与计数器清除函数
        self.auto_correct_count = 0     # 计数器清零
        self.chat_history = [{"role": "system", "content": sys_text}]
        self.ui.json_text.clear()
        self.ui.lineEdit.clear()
        QMessageBox.information(self, "提示", "记忆已清空")

    def on_execute(self):       #开始执行按钮的函数
        try:
            data = json.loads(self.ui.json_text.toPlainText())
            plans = data['plans']
        except (json.JSONDecodeError, KeyError):
            QMessageBox.warning(self, "错误", "请先点击生成配方")
            return
        self.ui.run_text.setText("----===开始进行JSON校验===-----")
        is_ok, err_list = validator_formula(data)    #调用JSON校验函数
        if not is_ok:
            # 错误列表显示
            error_message = "\n".join(err_list)
            for err in err_list:
                # 打印红色报错
                logger.error(err)
            # 在日志框打出红色警告
            self.ui.runing_text.append(
                f"<font color='red'>[系统拦截] 发现 {len(err_list)} 个致命逻辑错误，执行已中止！</font>")

            # 弹出严重警告弹窗
            QMessageBox.critical(
                self,
                "硬件执行拦截",
                f"配方逻辑校验未通过，为保护机械臂，已取消执行！\n\n详细诊断信息：\n{error_message}"
            )
            return
        else:
            # 安检通过，启动硬件
            logger.info("安检通过！正在归档配方数据...")

            #self.append_log("安检通过，准备启动物理机械臂...", level="INFO")
            logger.info("安检通过，准备启动物理机械臂...")
            self.ui.runing_text.append("<font color='green'>安检通过，准备唤醒机械臂...</font>")
            self.ui.execute_button.setEnabled(False)
            self.execute_worker = ExecuteWorker(self.robot, plans)
            self.execute_worker.finished.connect(self.on_execute_done)
            self.execute_worker.error.connect(self.on_execute_error)
            self.execute_worker.step_update.connect(self.on_step_update)
            self.execute_worker.led_update.connect(self.set_led)
            self.execute_worker.start()

    def on_execute_done(self, msg):     #解析成功
        self.ui.execute_button.setEnabled(True)
        logger.info(f"任务状态：{msg}")
        QMessageBox.information(self, "执行完成", msg)

    def on_execute_error(self, msg):    #解析失败
        self.ui.execute_button.setEnabled(True)
        logger.error(f"执行出现错误：{msg}")
        QMessageBox.warning(self, "错误", msg)

    def on_step_update(self, msg):      #更新状态
        self.ui.run_text.append("\n" + msg)     # 在界面上方的运行状态框中追加文字
        logger.info(f"[物理反馈] {msg}")
    def set_led(self, led_name, status):        #设置部件状态灯函数
        # status: 'idle'=灰色待机, 'running'=黄色运行, 'ok'=绿色正常
        colors = {
            'idle': 'gray',
            'running': '#FFC107',
            'ok': '#4CAF50'
        }
        color = colors.get(status, 'gray')
        try:
            led = getattr(self.ui, led_name)
            led.setStyleSheet(f"background-color:{color}; border-radius:8px;")
        except:
            logger.error(f"未能在界面上找到名为 {led_name} 的指示灯控件")

    def on_save_clicked(self):      #手动保存配方的槽函数

        # 抓取 UI 文本框中的最新内容
        json_raw = self.ui.json_text.toPlainText().strip()

        # 空内容拦截
        if not json_raw or "正在生成" in json_raw:
            logger.warning("当前配方框为空或正在生成，拒绝保存")
            QMessageBox.warning(self, "保存失败", "当前没有可用的配方内容。")
            return

        try:
            # 将字符串转为字典
            data = json.loads(json_raw)

            # 提取任务名作为文件名，如果没写则用默认值
            task_name = data.get("task_name", "manual_save")

            # 生成物理路径
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # 过滤掉文件名中的非法字符
            safe_task_name = "".join([c for c in task_name if c.isalnum() or c in ("_", "-")])
            filename = f"{safe_task_name}_{timestamp}.json"
            filepath = os.path.join(self.recipe_dir, filename)

            # 物理写入
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)

            logger.info(f"配方已手动存档：{filepath}")
            QMessageBox.information(self, "保存成功", f"配方已存至：\n{filename}")

        except json.JSONDecodeError as e:
            logger.error(f"保存失败：配方框内的 JSON 格式有误。{str(e)}")
            QMessageBox.critical(self, "格式错误", "文本框内的内容不是标准的 JSON 格式，请检查。")
        except Exception as e:
            logger.error(f"保存过程中发生未知错误：{str(e)}")



if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
