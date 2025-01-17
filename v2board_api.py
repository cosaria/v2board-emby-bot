import os
import requests
from dotenv import load_dotenv

class V2BoardAPI:
    def __init__(self):
        # 加载.env文件
        load_dotenv()

        # 获取配置
        self.base_url = os.getenv('V2BOARD_URL').rstrip('/')
        self.email = None
        self.password = None
        self.auth_data = None

        # 设置请求头
        self.headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def login(self):
        """登录并获取auth_data"""
        if not self.email or not self.password:
            return False

        url = f"{self.base_url}/passport/auth/login"
        data = { "email": self.email, "password": self.password }

        try:
            response = requests.post(url, json=data, headers=self.headers)
            if response.status_code == 200:
                result = response.json()
                if 'data' in result and 'auth_data' in result['data']:
                    self.auth_data = result['data']['auth_data']
                    self.headers['Authorization'] = self.auth_data
                    return True
            return False
        except Exception as e:
            print(f"Login error: {str(e)}")
            return False

    def check_auth(self):
        """检查认证是否有效"""
        if not self.auth_data:
            return False
        try:
            response = requests.get(
                f"{self.base_url}/user/info", headers=self.headers)
            return response.status_code == 200 and 'data' in response.json()
        except:
            return False

    def get_user_info(self):
        """获取用户信息"""
        if not self.auth_data:
            return None
        try:
            response = requests.get(
                f"{self.base_url}/user/info", headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Get user info error: {str(e)}")
            return None

    def get_subscribe_info(self):
        """获取订阅信息"""
        if not self.auth_data:
            return None
        try:
            response = requests.get(
                f"{self.base_url}/user/getSubscribe", headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Get subscribe info error: {str(e)}")
            return None

    def get_plan_list(self):
        """获取订阅商店列表"""
        if not self.auth_data:
            return None
        try:
            response = requests.get(
                f"{self.base_url}/user/plan/fetch", headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Get plan list error: {str(e)}")
            return None

    def get_order_list(self):
        """获取订单列表"""
        if not self.auth_data:
            return None
        try:
            response = requests.get(
                f"{self.base_url}/user/order/fetch", headers=self.headers)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Get order list error: {str(e)}")
            return None

    def create_order(self, plan_id, cycle):
        """创建订单"""
        if not self.auth_data:
            return None
        try:
            url = f"{self.base_url}/user/order/save"
            data = {
                "plan_id": plan_id,
                "cycle": cycle
            }
            response = requests.post(url, headers=self.headers, json=data)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            print(f"Create order error: {str(e)}")
            return None

def main():
    # 使用示例
    api = V2BoardAPI()

    # 如果没有token，先登录
    if not api.auth_data:
        if api.login():
            print("登录成功")
        else:
            print("登录失败")
            return

    # 获取用户信息
    user_info = api.get_user_info()
    if user_info:
        print("用户信息:", user_info)

    # 获取订阅信息
    subscribe_info = api.get_subscribe_info()
    if subscribe_info:
        print("订阅信息:", subscribe_info)

if __name__ == "__main__":
    main()
