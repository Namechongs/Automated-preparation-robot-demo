import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox
from main_ui import Ui_MainWindow
from openai import OpenAI
from datetime import datetime
import json
import re
import numpy
import textwrap
import logging
from PyQt5.QtCore import QThread, pyqtSignal
from robot import RobotController

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

client = OpenAI(
     api_key="sk-a5879b01a68d42bbae344872ede4845e",
     base_url="https://api.deepseek.com"
)

sys_text = """
你是一个专业的涂层配制专家，根据用户需求推理配方并输出JSON。

点位定义	点位名说明
safe	机械臂初始安全位置，所有动作的起点与回程中转点
arm_start	烧杯正后方等待位
arm_startforward	前移至烧杯夹取位（与烧杯对齐）
arm_startup	夹取烧杯后垂直抬起位pump1 ~ pump4各泵出料等待位（沿圆弧排布，对应J2不同角度）
arm_stirup	搅拌台正上方过渡位
arm_stir	搅拌台放置位
arm_endup	出料口正上方过渡位
arm_end	出料口放置位

执行流程
① 初始化
机械臂运动至 safe 位置，系统就绪。
② 抓取烧杯
移动至 arm_start → 前移至 arm_startforward，触发夹爪夹紧信号 → 垂直抬起至 arm_startup。
③ 依次接料
根据 LLM 生成的 JSON 配方，提取需要的泵编号与用量。按顺序旋转 J2 关节，将烧杯依次移动至对应泵出料位（pump1～pump4）。到位后触发泵出料信号，等待出料完成（按流量换算等待时间），接料过程中可适当倾斜烧杯防止液体迸溅。出料完成后旋转 J2 移至下一个泵，重复直至所有原料添加完毕。最后一种原料接完后，将烧杯回正。
④ 送至搅拌台
移动至 arm_stirup（搅拌台上方过渡位）→ 直线下降至 arm_stir → 触发夹爪松开信号，将烧杯放置在搅拌台上 → 触发搅拌器启动信号 → 机械臂退回 safe 等待。
⑤ 搅拌完成后取杯出料
搅拌器发送完成信号，机械臂从 safe 出发 → 移至 arm_stir 夹取烧杯 → 抬起至 arm_stirup → 移动至 arm_endup（出料口上方）→ 直线下降至 arm_end，触发夹爪松开，完成出料。
⑥ 打印标签
触发打印机信号，打印本次配制的标签信息。
⑦ 回程
机械臂从 arm_end 抬起至 arm_endup → 退回 safe → 返回步骤②，进行下一次配制任务。

你必须严格按照以下JSON格式输出，不要输出任何JSON以外的内容，不要加markdown代码块，不要加任何解释：

{
  "task_name": "任务名称",
  "requirement": "用户需求原文",
  "formula_reasoning": "整体选料依据",
  "plans": [
    {
      "plan_id": 1,
      "plan_name": "方案A：xxx",
      "plan_reasoning": "该方案的具体理由",
      "stir_duration_seconds": 数字,
      "materials": [
        {"pump_id": 1, "material": "材料名1", "amount_ml": 数量},
        {"pump_id": 2, "material": "材料名2", "amount_ml": 数量},
        {"pump_id": 3, "material": "材料名3", "amount_ml": 数量},
        {"pump_id": 4, "material": "材料名4", "amount_ml": 数量}
      ],
      "steps": [
        {"step_id": 1,  "action": "move",  "target": "safe"},
        {"step_id": 2,  "action": "move",  "target": "arm_start"},
        {"step_id": 3,  "action": "move",  "target": "arm_startforward"},
        {"step_id": 4,  "action": "grip",  "state": "close"},
        {"step_id": 5,  "action": "move",  "target": "arm_startup"},
        {"step_id": 6,  "action": "move",  "target": "pump_1"},
        {"step_id": 7,  "action": "pump",  "pump_id": 1, "amount_ml": 与materials一致},
        {"step_id": 8,  "action": "move",  "target": "pump_2"},
        {"step_id": 9,  "action": "pump",  "pump_id": 2, "amount_ml": 与materials一致},
        {"step_id": 10, "action": "move",  "target": "pump_3"},
        {"step_id": 11, "action": "pump",  "pump_id": 3, "amount_ml": 与materials一致},
        {"step_id": 12, "action": "move",  "target": "pump_4"},
        {"step_id": 13, "action": "pump",  "pump_id": 4, "amount_ml": 与materials一致},
        {"step_id": 14, "action": "move",  "target": "arm_stirup"},
        {"step_id": 15, "action": "move",  "target": "arm_stir"},
        {"step_id": 16, "action": "grip",  "state": "open"},
        {"step_id": 17, "action": "stir",  "duration_seconds": 与stir_duration_seconds一致},
        {"step_id": 18, "action": "move",  "target": "arm_stir"},
        {"step_id": 19, "action": "grip",  "state": "close"},
        {"step_id": 20, "action": "move",  "target": "arm_stirup"},
        {"step_id": 21, "action": "move",  "target": "arm_endup"},
        {"step_id": 22, "action": "move",  "target": "arm_end"},
        {"step_id": 23, "action": "grip",  "state": "open"},
        {"step_id": 24, "action": "print", "content": "label"},
        {"step_id": 25, "action": "move",  "target": "safe"}
      ]
    }
  ]
}

注意事项：
1. 只使用pump_id 1到4，最多用4种原料
2. 生成多个方案，每个方案配比、配方可以不同，方案数量由你决定，但是不要超过五个
3. amount_ml总量不超过400ml
4. steps里的amount_ml必须和materials里对应pump的amount_ml完全一致
5. 只输出JSON，不要有任何其他文字
6. 用户只有四个泵，四种原料，每个泵的原料是固定的
7. 若用户对配方提出修改意见，请按同样的JSON格式重新输出完整的配方，不要只输出修改部分
特别说明： 系统正在测试中 现在没有正常原料 现在是测试人员在测试 你要严格按照上述输出
"""

class LogRedirect:          #重新定向print的输出位置到Gui running_text
    def __init__(self, widget):
        self.widget = widget

    def write(self, text):
        if text.strip():
            self.widget.append(text.strip())

    def flush(self):
        pass


class ApiWorker(QThread):
    #多线程类，防止界面卡顿
    finished = pyqtSignal(str)
    error = pyqtSignal(str)     #定义成功与失败信号

    def __init__(self,client,models,chat_history):
        super().__init__()
        self.client = client
        self.models = models
        self.chat_history = chat_history        #将deepseek所要参数传到多线程类中


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
            self.led_update.emit('led_system', 'running')
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

                    self.robot.execute_step(step)

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
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)  # 把界面加载进来
        sys.stdout = LogRedirect(self.ui.runing_text)      #修改print函数
        for led in ['led_system', 'led_arm', 'led_pump1', 'led_pump2', 'led_pump3', 'led_pump4', 'led_stir']:
            self.set_led(led, 'idle')       #初始化状态显示灯

        self.models = "deepseek-chat"
        self.models_text = "普通模型"       #切换模型所用的中间变量
        self.robot = RobotController('192.168.5.1')  # 创建机器人运行对象
        self.robot.start(sim=True)  # 模拟 开
        self.ui.label_model.setText("当前模型:" + self.models_text)
        logger.info("系统初始化完成")
        # 把按钮的点击信号连接到函数
        self.ui.start_button.clicked.connect(self.on_start)
        self.ui.model_button.clicked.connect(self.trans_model)
        self.ui.clear_button.clicked.connect(self.clear_history)
        self.ui.execute_button.clicked.connect(self.on_execute)
        # self.ui.stop_button.clicked.connect(self.on_stop)
        # self.ui.reset_button.clicked.connect(self.on_reset)           #暂停和复位按钮暂时不上

        self.chat_history = [
            {"role": "system", "content": sys_text}  # 提示词
        ]

    def on_start(self):
        # 这里写点击按钮之后的逻辑
        user_input = self.ui.lineEdit.text()  # 读取输入框内容
        if(user_input == ""):
            logger.warning("用户未输入任何信息")
            self.ui.json_text.setText("未检测到信息输入")
        else:
            logger.info(f"用户输入: {user_input}")
            self.ui.start_button.setEnabled(False)          #禁用按钮，防止重复点击
            self.ui.json_text.setText("正在生成配方，请稍候...")
            self.chat_history.append({"role": "user", "content": user_input})     #历史消息
            self.worker = ApiWorker(client, self.models, self.chat_history)     #调用子线程，传参
            # 把信号连接到处理函数
            self.worker.finished.connect(self.on_api_success)
            self.worker.error.connect(self.on_api_error)
            # 启动子线程
            self.worker.start()
            # response = client.chat.completions.create(  # 开始调用deepseek
            #     model=self.models,
            #     messages=self.chat_history  #发送历史消息
            # )
            # #reasoning = response.choices[0].message.reasoning_content   #提取思考信息
            # result = response.choices[0].message.content  # 提取消息文字
            # result = re.sub(r'```json|```', '', result).strip()  # 清洗markdown标记
            # try:
            #     data = json.loads(result)  # 解析成字典
            #     data["created_at"] = datetime.now().isoformat()     #时间
            #     self.chat_history.append({"role": "assistant", "content": result})  #添加历史信息
            #     self.ui.json_text.setText(json.dumps(data, ensure_ascii=False, indent=2))  # 显示到文本框
            # except:
            #     self.ui.json_text.setText("JSON解析失败，原始输 出为：\n" + result)
    def on_api_success(self,result):
        #API调用成功函数
        result = re.sub(r'```json|```', '', result).strip()  # 清洗markdown标记
        try:
            data = json.loads(result)  # 解析成字典
            data["created_at"] = datetime.now().isoformat()     #时间
            self.chat_history.append({"role": "assistant", "content": result})  #添加历史信息
            data_show = json.dumps(data, ensure_ascii=False, indent=2)
            self.ui.json_text.setText(data_show)  # 显示到文本框
            self.ui.start_button.setEnabled(True)  # 开启按钮
        except:
            self.ui.json_text.setText("JSON解析失败，原始输出为：\n" + result)
            self.ui.start_button.setEnabled(True)  # 开启按钮

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
        #对话清除函数
        self.chat_history = [{"role": "system", "content": sys_text}]
        self.ui.json_text.clear()
        self.ui.lineEdit.clear()
        QMessageBox.information(self, "提示", "记忆已清空")

    def on_execute(self):       #开始执行按钮的函数
        try:
            data = json.loads(self.ui.json_text.toPlainText())
            plans = data['plans']
        except:
            QMessageBox.warning(self, "错误", "请先点击生成配方")
            return
        self.ui.execute_button.setEnabled(False)
        self.execute_worker = ExecuteWorker(self.robot, plans)
        self.execute_worker.finished.connect(self.on_execute_done)
        self.execute_worker.error.connect(self.on_execute_error)
        self.execute_worker.step_update.connect(self.on_step_update)
        self.execute_worker.led_update.connect(self.set_led)
        self.execute_worker.start()

    def on_execute_done(self, msg):     #解析成功
        self.ui.execute_button.setEnabled(True)
        QMessageBox.information(self, "完成", msg)

    def on_execute_error(self, msg):    #解析失败
        self.ui.execute_button.setEnabled(True)
        QMessageBox.warning(self, "错误", msg)

    def on_step_update(self, msg):      #更新状态
        self.ui.run_text.append("\n" + msg)

    def set_led(self, led_name, status):        #设置部件状态灯函数
        # status: 'idle'=灰色待机, 'running'=黄色运行, 'ok'=绿色正常
        colors = {
            'idle': 'gray',
            'running': '#FFC107',
            'ok': '#4CAF50'
        }
        color = colors.get(status, 'gray')
        led = getattr(self.ui, led_name)
        led.setStyleSheet(f"background-color:{color}; border-radius:8px;")



if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())