import serial
import dobot_api
import logging
from dobot_api import DobotApiDashboard, DobotApiFeedBack
from pymodbus.client import ModbusSerialClient
import time
import atexit

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class StirrerController:        # 搅拌器控制类
    def __init__(self, port: str, baudrate: int = 9600, simulate: bool = False):
        self.simulate = simulate
        if not self.simulate:
            self.ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,  # 数据位8位
                parity=serial.PARITY_NONE,  # 无奇偶校验
                stopbits=serial.STOPBITS_ONE,  # 停止位1位
                timeout=1.0  # 超时1s
            )
    def check_sum(self, data: list[int]):       # 校验和
        return sum(data) & 0xff
    def send(self, cmd: int, p1=0, p2=0, p3=0):     # 搅拌器RS232发送数据函数
        payload = [cmd, p1, p2, p3]     # 合并数据位
        send_data = bytes([0xFE] + payload + [self.check_sum(payload)])      #整合发送的数据
        if self.simulate:
            print("[模拟] 搅拌器数据发送成功，使能完毕")
            logger.info("搅拌器数据发送成功，使能完毕（模拟模式）")
            return None
        self.ser.write(send_data)   #发送数据
        time.sleep(0.05)        # 等待仪器处理
        return self.ser.read(11)    # 读取11字节

    def handshake(self):        # 检查握手是否正常
        resp = self.send(0xA0)
        if self.simulate:
            return True
            # resp[0]=0xFD是仪器回包帧头，resp[2]=0x00表示执行成功
        return resp is not None and len(resp) >= 3 and resp[0] == 0xFD and resp[2] == 0x00

    def speed_send(self, rpm: int):
        # 先发高位 后发低位
        high = (rpm >> 8) & 0xff
        low = rpm & 0xFF
        self.send(0xC0, high, low, 0)       # 控制转速
    def start(self):
        # 开启搅拌，参数1=1
        self.send(0xC3, 1, 0, 0)

    def stop(self):
        # 停止搅拌，参数1=0
        self.send(0xC3, 0, 0, 0)

    def get_speed(self) -> dict:
        # 查询转速，响应11字节
        # 字节3-4是设定转速，字节5-6是实际转速
        # 拼回16位整数：(高字节 << 8) | 低字节
        resp = self.send(0xC6)
        if self.simulate or resp is None or len(resp) < 6:
            return {"set": 0, "actual": 0}
        set_rpm = (resp[2] << 8) | resp[3]
        actual_rpm = (resp[4] << 8) | resp[5]
        return {"set": set_rpm, "actual": actual_rpm}

    def wait_for_speed(self, target_rpm: int, tolerance: int = 20, timeout: int = 30):
        """
        轮询实际转速，等到接近目标转速后返回，再开始计时。
        tolerance: 允许的误差范围，实际转速在 target±tolerance 内视为到位
        timeout:   最多等多少秒，超时返回False
        """
        if self.simulate:
            return True  # 模拟模式
        deadline = time.time() + timeout
        while time.time() < deadline:
            actual = self.get_speed()["actual"]
            if abs(actual - target_rpm) <= tolerance:
                return True  # 转速到位
            time.sleep(0.5)  # 每0.5秒查一次
        return False  # 超时

    def stir(self, rpm: int, duration: int):
        """
        完整搅拌流程：设速 → 启动 → 等转速到位 → 计时 → 停止
        duration: 搅拌时长（秒），从转速到位后开始计
        """
        self.speed_send(rpm)
        self.start()

        # 等转速爬升到位，到位后再开始计时
        reached = self.wait_for_speed(rpm)
        if not reached:
            # 超时还没到位
            print(f"[警告] 转速未在规定时间内到达 {rpm} rpm，继续执行")
            logger.warning(f"[警告] 转速未在规定时间内到达 {rpm} rpm，继续执行")

        # 转速到位，开始计时
        time.sleep(duration)
        self.stop()

    def close(self):        # 关机
        if not self.simulate and hasattr(self, 'ser'):
            self.ser.close()

class RobotController:  # 总控制类
    def __init__(self, ip, simulate=False):
        self.ip = ip
        self.simulate = simulate
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

    def start(self, port_pump, port_stir):
        logger.info(f"机器人启动，IP地址: {self.ip}, 模拟模式: {self.simulate}")

        # 先关掉旧连接，防止切换串口时泄漏
        if hasattr(self, 'stirrer') and self.stirrer is not None:
            self.stirrer.close()
        if hasattr(self, 'client') and self.client is not None:
            self.client.close()
        # 搅拌器
        self.stirrer = StirrerController(port=port_stir, simulate=self.simulate)
        # 泵
        self.client = ModbusSerialClient(
            port = port_pump,
            baudrate = 9600,
            bytesize = 8,
            parity = 'N',
            stopbits = 1,
            timeout = 1
        )

        if self.simulate:
            print("[模拟] 机器人连接成功，使能完毕")
            logger.info("机器人连接成功，使能完毕（模拟模式）")
            self.connected = True
            return
            # 真机模式连接泵
        if self.client.connect():
            logger.info("泵控制器连接成功")
        else:
            logger.error("泵控制器连接失败，检查COM口和接线")
        if self.stirrer.handshake():
            logger.info("搅拌器连接成功")
        else:
            logger.error("搅拌器握手失败，检查串口")
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

    def pump(self, pump_id, amount_ml):

        if pump_id == 1:
            FLOWRATE = 30
            TUBE_CODE = 0x02  # 3.17*0.8 管子
        else:  # 泵2,3,4
            FLOWRATE = 30
            TUBE_CODE = 0x05  # 3*1 管子

        logger.info(f"泵 {pump_id} 开始出料，剂量: {amount_ml}ml")

        if self.simulate:
            wait_time = amount_ml / 10
            time.sleep(wait_time)
            print(f"[模拟] 泵{pump_id} 出料完成")
            logger.info(f"泵 {pump_id} 出料完成")
            return

        # 真机部分
        base = pump_id << 12        # 泵通道ID

        # 1. 设置模式
        self.client.write_register(base | 0x004, 1, device_id=1)
        time.sleep(0.1)

        # 2. 硬件参数
        self.client.write_register(base | 0x005, TUBE_CODE, device_id=1)    # 设置软管型号
        time.sleep(0.1)
        self.client.write_register(base | 0x001, 1, device_id=1)  # 正转
        time.sleep(0.1)
        self.client.write_register(base | 0x003, 1, device_id=1)  # 使能
        time.sleep(0.1)

        # 3. 计算并写入分配参数
        amount_ul = int(amount_ml * 1000)       # 单位uL
        disp_sec = (amount_ml / FLOWRATE) * 60 * 1.2
        disp_reg = int(disp_sec * 100)

        self.client.write_registers(base | 0x020, [
            0x0000, amount_ul,  # 液量 uint32
            0x0000, disp_reg,  # 分配时间 uint32
            0x0000, 0,  # 间隔时间 uint32
            1  # 分配次数
        ], device_id=1)
        time.sleep(0.1)

        # 4. 启动
        self.client.write_register(base | 0x000, 1, device_id=1)
        logger.info(f"泵{pump_id} 出料中，预计等待{disp_sec:.1f}秒")

        # 5. 阻塞等待
        time.sleep(disp_sec)

        # 6. 停止
        self.client.write_register(base | 0x000, 0, device_id=1)
        # 7. 会吸
        suck_angle = 1800  # 180度
        self.client.write_register(base | 0x006, suck_angle, device_id=1)
        time.sleep(0.5)

        logger.info(f"泵 {pump_id} 出料完成，已回吸{suck_angle/10}°")

        def shutdown(self):  # 关机函数
            logger.info("系统关机，停止所有设备")
            try:
                if hasattr(self, 'stirrer') and self.stirrer is not None:
                    self.stirrer.stop()
                    self.stirrer.close()
            except Exception as e:
                logger.error(f"搅拌器关机失败: {e}")

            try:
                if hasattr(self, 'client') and self.client is not None:
                    # 停所有泵
                    for pump_id in range(1, 5):
                        base = pump_id << 12
                        self.client.write_register(base | 0x000, 0, device_id=1)
                    self.client.close()
            except Exception as e:
                logger.error(f"泵关机失败: {e}")

    # def stir(self, times, stir_speed):      #搅拌函数
    #     # 具体指令等设备说明书确定后填写
    #     # 此行为真实串口输出信息函数，待书写
    #
    #
    #     print(f"[模拟] 搅拌开始")
    #     logger.info(f"搅拌开始，时长: {times}秒,速度{stir_speed}rpm")
    #     time.sleep(times + 2)
    #     print(f"[模拟] 搅拌完成")
    #     logger.info("搅拌完成")







