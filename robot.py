import dobot_api
import time
import logging
from dobot_api import DobotApiDashboard, DobotApiFeedBack

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class RobotController:
    def __init__(self,ip):
        self.ip = ip
        self.connected = False
        # self.dashboardPort = 29999
        # self.feedPortFour = 30004
        # self.dashboard = None
        # self.feedInfo = []
        # self.__globalLockValue = threading.Lock()         #连接真机再用
        self.POINT = {      #点位坐标阵列 到时候可以设置为可调 只需要测量工作台点位即可实现扩展

            'safe':             [0,    0,   400, 0,   0, 0],
            'arm_start':        [300,  0,   200, 180, 0, 0],
            'arm_startforward': [350,  0,   200, 180, 0, 0],
            'arm_startup':      [350,  0,   350, 180, 0, 0],
            'arm_stirup':       [200,  200, 350, 180, 0, 0],
            'arm_stir':         [200,  200, 180, 180, 0, 0],
            'arm_endup':        [-200, 200, 350, 180, 0, 0],
            'arm_end':          [-200, 200, 180, 180, 0, 0],
            'pump_1':            [100,  200, 300, 400, 0, 0],
            'pump_2':            [100,  200, 300, 400, 0, 0],
            'pump_3':            [100,  200, 300, 400, 0, 0],
            'pump_4':            [100,  200, 300, 400, 0, 0]

        }
        class item:
        #这是一个反馈数据的类 现在还没有 到时候再添加
            def __init__(self):
                self.robotMode = -1

        self.feeddata = item()  #定义反馈的数据对象

    def start(self,sim):
        logger.info(f"机器人启动，IP地址: {self.ip}, 模拟模式: {sim}")
        if sim:
            print("[模拟] 机器人连接成功，使能完毕")
            logger.info("机器人连接成功，使能完毕（模拟模式）")
            self.connected = True
            return
        #真机时候取消注释
        # self.dashboard = DobotApiDashboard(self.ip,29999)
        # self.feedboard = DobotApiFeedBack(self.ip, 30004)     #创建控制通道与反馈通道
        # self.dashboard.EnableRobot()     #机器人使能
        # self.dashboard.SpeedFactor(30)   #设置全局速度

    def move_to(self,point_name,move_model):        #位移函数 输入点名称与运动模式（J为点对点普通位移 L为严格直线位移）
        if point_name not in self.POINT:
            print("输入了错误的点位")
            logger.error(f"移动失败：未知的点位 {point_name}")
            return
        p = self.POINT[point_name]
        print(f"[模拟] 移动到 {point_name} {p}")
        logger.info(f"移动到 {point_name}，坐标: {p}，运动模式: {move_model}")
        time.sleep(2)
        #真机时候取消注释
        # if move_model == 'J':
        #     self.dashboard.MovJ(p[0], p[1], p[2], p[3], p[4], p[5], 0)
        # elif move_model == 'L':
        #     self.dashboard.MovL(p[0], p[1], p[2], p[3], p[4], p[5], 0)
        # self.wait_arrive()  # 等到位了再返回
    def grip(self,state):
        if state == "open":
            #self.dashboard.DO(1, 0)
            time.sleep(1)
            print("[模拟]机械爪已松开")
            logger.info("机械爪已松开")
        elif state == "close":
            #self.dashboard.DO(1, 1)
            time.sleep(1)
            print("[模拟]机械爪已夹紧")
            logger.info("机械爪已夹紧")
        else:
            print("[模拟]机械爪状态错误")        #错误状态输出 后期添加到GUi报错信息
            logger.error(f"机械爪状态错误: {state}")

    def pump(self, pump_id, amount_ml):     #泵输出函数
        # 具体指令等设备说明书确定后填写
        # 此行为真实串口输出信息函数，待书写
        print(f"[模拟] 泵{pump_id} 出料 {amount_ml}ml")
        logger.info(f"泵 {pump_id} 开始出料，剂量: {amount_ml}ml")
        wait_time = amount_ml / 10
        time.sleep(wait_time)
        print(f"[模拟] 泵{pump_id} 出料完成")
        logger.info(f"泵 {pump_id} 出料完成")


    def stir(self, times):      #搅拌函数
        # 具体指令等设备说明书确定后填写
        # 此行为真实串口输出信息函数，待书写
        print(f"[模拟] 搅拌开始")
        logger.info(f"搅拌开始，时长: {times}秒")
        time.sleep(times + 2)
        print(f"[模拟] 搅拌完成")
        logger.info("搅拌完成")

    def execute_step(self, step):
        action = step['action']
        print(f"执行步骤 {step['step_id']}: {action}")
        logger.info(f"执行步骤 {step['step_id']}: {action}")
        if action == 'move':
            target = step['target']
            mode = 'L' if target in ['arm_stir', 'arm_end'] else 'J'
            self.move_to(target, mode)
        elif action == 'grip':
            self.grip(step['state'])
        elif action == 'pump':
            self.pump(step['pump_id'], step['amount_ml'])
        elif action == 'stir':
            self.stir(step['duration_seconds'])
        elif action == 'print':
            print("[模拟] 打印成功")
            logger.info("标签打印成功")




