import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from v2board_api import V2BoardAPI
from emby_api import EmbyAPI
import time
from logging.handlers import TimedRotatingFileHandler

# 配置日志
# 创建日志目录
log_directory = "./logs"
os.makedirs(log_directory, exist_ok=True)
# 设置日志文件名为当前日期的格式
log_filename = os.path.join(log_directory, "bot.log")

logging.basicConfig(
    format='%(asctime)s - [%(levelname)s] - %(filename)s:%(lineno)d - %(funcName)s - %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
            TimedRotatingFileHandler(
                log_filename, when="midnight", encoding="utf-8", backupCount=30),  # 每天生成新的日志文件
            logging.StreamHandler(),  # 同时输出到控制台

    ]
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx").propagate = False

# 加载环境变量
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# 定义会话状态
TYPING_EMAIL = 0
TYPING_PASSWORD = 1

# 创建用户数据目录，如果目录不存在，则创建
USER_DATA_DIR = Path("user_data")
if not USER_DATA_DIR.exists():
    USER_DATA_DIR.mkdir(exist_ok=True)

# 用户会话数据（内存中的临时存储）
user_data = {}
# 用户数据最后访问时间
user_last_access = {}
# 数据过期时间（秒）
DATA_EXPIRE_TIME = 300  # 5分钟不活动就清除数据

# 邮箱到用户ID的映射缓存
email_user_map = {}
EMAIL_MAP_FILE = Path("email_map.json")


def load_email_map():
    """加载邮箱映射数据"""
    global email_user_map
    try:
        if EMAIL_MAP_FILE.exists():
            with open(EMAIL_MAP_FILE, 'r', encoding='utf-8') as f:
                email_user_map = json.load(f)
        else:
            # 如果映射文件不存在，则重建索引
            rebuild_email_map()
    except Exception as e:
        logger.error(f"加载邮箱映射数据出错: {str(e)}")
        email_user_map = {}


def save_email_map():
    """保存邮箱映射数据"""
    try:
        with open(EMAIL_MAP_FILE, 'w', encoding='utf-8') as f:
            json.dump(email_user_map, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存邮箱映射数据出错: {str(e)}")


def rebuild_email_map():
    """重建邮箱映射索引"""
    global email_user_map
    email_user_map = {}
    for file_path in USER_DATA_DIR.glob("*.json"):
        try:
            user_id = int(file_path.stem)
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('email'):
                    email_user_map[data['email']] = user_id
        except Exception as e:
            logger.error(f"重建邮箱索引时出错: {str(e)}")
    save_email_map()


def check_email_usage(email: str, current_user_id: int) -> bool:
    """检查邮箱是否被其他Telegram账号使用（优化版）"""
    # 确保映射已加载
    if not email_user_map:
        load_email_map()

    # 检查邮箱是否已被使用
    if email in email_user_map:
        existing_user_id = email_user_map[email]
        if existing_user_id != current_user_id:
            logger.warning(f"邮箱 {email}(tg:{existing_user_id}) 已被其他用户使用")
            return False
    return True


def check_and_clean_old_binding(email: str, current_user_id: int) -> bool:
    """检查邮箱绑定并清理旧的绑定"""
    try:
        # 确保映射已加载
        if not email_user_map:
            load_email_map()

        # 检查邮箱是否已被其他用户绑定
        if email in email_user_map:
            old_user_id = email_user_map[email]
            if old_user_id != current_user_id:
                logger.info(
                    f"邮箱 {email}(tg:{old_user_id}) 正在被新用户(tg:{current_user_id})绑定，清理旧用户数据")

                # 删除旧用户的Emby账号
                if old_user_id in user_data and user_data[old_user_id].get('emby'):
                    try:
                        emby = EmbyAPI()
                        emby_user_id = user_data[old_user_id]['emby']['user_id']
                        emby.delete_user(emby_user_id)
                        logger.info(f"已删除用户 {email}(tg:{old_user_id}) 的Emby账号")
                    except Exception as e:
                        logger.error(
                            f"删除用户 {email}(tg:{old_user_id}) 的Emby账号时出错: {str(e)}")

                # 清理旧用户的数据
                if old_user_id in user_data:
                    del user_data[old_user_id]
                if old_user_id in user_last_access:
                    del user_last_access[old_user_id]

                # 删除旧用户的数据文件
                old_file_path = USER_DATA_DIR / f"{old_user_id}.json"
                if old_file_path.exists():
                    old_file_path.unlink()
                    logger.info(f"已删除用户 {email}(tg:{old_user_id}) 的数据文件")

                # 从邮箱映射中删除旧绑定
                del email_user_map[email]
                save_email_map()

        return True
    except Exception as e:
        logger.error(f"清理旧绑定时出错: {str(e)}")
        return False

# 在保存用户数据时更新邮箱映射


def save_user_data(user_id: int, data: dict):
    """保存用户数据到json文件（更新版）"""
    file_path = USER_DATA_DIR / f"{user_id}.json"
    # 只保存需要持久化的数据
    save_data = {
        'email': data.get('email'),
        'password': data.get('password'),
        'auth_data': data.get('api').auth_data if data.get('api') else None,
        'emby': data.get('emby', {})
    }
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    # 更新邮箱映射
    if save_data.get('email'):
        email_user_map[save_data['email']] = user_id
        save_email_map()


async def clean_expired_data(context: ContextTypes.DEFAULT_TYPE | None = None) -> None:
    """清理过期的用户数据"""
    current_time = time.time()
    expired_users = [
        user_id for user_id, last_access in user_last_access.items()
        if current_time - last_access > DATA_EXPIRE_TIME
    ]
    for user_id in expired_users:
        email = user_data[user_id].get('email', 'unknown')
        if user_id in user_data:
            del user_data[user_id]
        del user_last_access[user_id]
        logger.info(f"已清理用户 {email}(tg:{user_id}) 的过期数据")


def load_user_data(user_id: int) -> dict:
    """从json文件加载用户数据"""
    file_path = USER_DATA_DIR / f"{user_id}.json"
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                result = {}
                email = data.get('email', 'unknown')
                user_identifier = f"{email}(tg:{user_id})"

                # 检查是否有必要的登录信息
                if data.get('email') and data.get('password'):
                    api = V2BoardAPI()
                    api.email = data['email']
                    api.password = data['password']

                    # 如果有auth_data，先尝试使用它
                    if data.get('auth_data'):
                        api.auth_data = data['auth_data']
                        api.headers['Authorization'] = data['auth_data']

                        # 验证auth_data是否有效
                        user_info = api.get_user_info()
                        if user_info and 'data' in user_info:
                            logger.info(f"用户 {user_identifier} 的认证数据有效")
                            result = {
                                'email': data['email'],
                                'password': data['password'],
                                'api': api,
                                'emby': data.get('emby', {})
                            }
                            return result
                        else:
                            logger.warning(
                                f"用户 {user_identifier} 的认证已过期，尝试重新登录")

                    # 如果auth_data无效或不存在，尝试重新登录
                    if api.login():
                        logger.info(f"用户 {user_identifier} 自动重新登录成功")
                        # 更新存储的认证数据
                        data['auth_data'] = api.auth_data
                        with open(file_path, 'w', encoding='utf-8') as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)

                        result = {
                            'email': data['email'],
                            'password': data['password'],
                            'api': api,
                            'emby': data.get('emby', {})
                        }
                        return result
                    else:
                        logger.warning(f"用户 {user_identifier} 重新登录失败")

                # 如果无法登录，返回不带API的数据
                return {
                    'email': data.get('email'),
                    'password': data.get('password'),
                    'emby': data.get('emby', {})
                }

        except Exception as e:
            logger.error(f"加载用户 {user_identifier} 数据时出错: {str(e)}")
    return {}


async def load_user_session(update: Update) -> bool:
    """加载用户会话数据，如果成功加载返回True"""
    user_id = update.effective_user.id

    # 清理过期数据
    await clean_expired_data()

    # 如果数据不在内存中，从文件加载
    if user_id not in user_data:
        user_data[user_id] = load_user_data(user_id)

    # 更新最后访问时间
    user_last_access[user_id] = time.time()

    return bool(user_data[user_id].get('api'))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user = update.effective_user
    # 尝试加载用户数据
    is_logged_in = await load_user_session(update)

    welcome_message = f"您好 {user.mention_html()}！\n"
    welcome_message += "欢迎使用 V2Board 管理机器人。\n"
    if not is_logged_in:
        welcome_message += "使用 /login 登录您的账号。\n"
    else:
        welcome_message += "您已登录，可以直接使用各项功能。\n"
    welcome_message += "使用 /help 查看所有可用命令。"

    await update.message.reply_html(welcome_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    help_text = """
可用命令列表：
/start - 开始使用机器人
/help - 显示此帮助信息
/login - 登录账号
/info - 查看账户信息
/subscribe - 获取订阅信息
/plans - 查看可用套餐
/orders - 查看订单列表
/create_emby - 创建Emby账号
/emby_info - 查看Emby账号信息
/delete_emby - 删除Emby账号
"""
    await update.message.reply_text(help_text)


async def login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始登录流程"""
    await update.message.reply_text("请输入您的邮箱地址：")
    return TYPING_EMAIL


async def email_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理邮箱输入"""
    user_data[update.effective_user.id] = {'email': update.message.text}
    await update.message.reply_text("请输入您的密码：")
    return TYPING_PASSWORD


async def password_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理密码输入并尝试登录"""
    user_id = update.effective_user.id
    email = user_data[user_id]['email']
    password = update.message.text

    # 删除密码消息以保护隐私
    await update.message.delete()

    # 创建API实例并尝试登录
    api = V2BoardAPI()
    api.email = email
    api.password = password

    try:
        if api.login():
            # 清理该邮箱的旧绑定
            if not check_and_clean_old_binding(email, user_id):
                await update.message.reply_text("处理账号绑定时出错，请重试")
                return ConversationHandler.END

            user_data[user_id].update({
                'api': api,
                'password': password
            })
            # 保存用户数据
            save_user_data(user_id, user_data[user_id])
            await update.message.reply_text("登录成功！现在您可以使用其他命令了。")
        else:
            await update.message.reply_text("登录失败：账号或密码错误")
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        await update.message.reply_text("登录失败：网络错误")

    return ConversationHandler.END


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """获取用户信息"""
    if not await load_user_session(update):
        await update.message.reply_text("请先使用 /login 登录")
        return

    try:
        api = user_data[update.effective_user.id]['api']
        info = api.get_user_info()
        if info and 'data' in info:
            info = info['data']
            message = f"""
账户信息：
邮箱：{info.get('email', 'N/A')}
余额：{info.get('balance', 0)} 元
流量：{info.get('transfer_enable', 0) / 1024 / 1024 / 1024:.2f} GB
过期时间：{info.get('expired_at', 'N/A')}
当前订阅id：{info.get('plan_id', 'N/A')}
"""
            await update.message.reply_text(message)
        else:
            # 如果获取信息失败，可能是认证过期
            await update.message.reply_text("获取信息失败，请重新登录")
    except Exception as e:
        logger.error(f"Info error: {str(e)}")
        await update.message.reply_text("获取信息失败：网络错误")


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """获取订阅信息"""
    if not await load_user_session(update):
        await update.message.reply_text("请先使用 /login 登录")
        return

    try:
        api = user_data[update.effective_user.id]['api']
        sub_info = api.get_subscribe_info()
        if sub_info and 'data' in sub_info:
            sub_info = sub_info['data']
            message = f"""
订阅信息：
订阅链接：{sub_info.get('subscribe_url', 'N/A')}
已用上行：{sub_info.get('u', 0) / 1024 / 1024 / 1024:.2f} GB
已用下行：{sub_info.get('d', 0) / 1024 / 1024 / 1024:.2f} GB
总流量：{sub_info.get('transfer_enable', 0) / 1024 / 1024 / 1024:.2f} GB
"""
            await update.message.reply_text(message)
        else:
            # 如果获取信息失败，可能是认证过期
            await update.message.reply_text("获取订阅信息失败，请重新登录")
    except Exception as e:
        logger.error(f"Subscribe error: {str(e)}")
        await update.message.reply_text("获取订阅信息失败：网络错误")


async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """获取套餐列表"""
    if not await load_user_session(update):
        await update.message.reply_text("请先使用 /login 登录")
        return

    try:
        api = user_data[update.effective_user.id]['api']
        plans = api.get_plan_list()
        if plans and 'data' in plans:
            plans = plans['data']
            message = "可用套餐列表：\n\n"
            for plan in plans:
                message += f"""
名称：{plan.get('name', 'N/A')}
月付：{plan.get('month_price', 'N/A')} 元
季付：{plan.get('quarter_price', 'N/A')} 元
年付：{plan.get('year_price', 'N/A')} 元
流量：{plan.get('transfer_enable', 0) / 1024 / 1024 / 1024:.2f} GB
------------------------
"""
            await update.message.reply_text(message)
        else:
            # 如果获取信息失败，可能是认证过期
            await update.message.reply_text("获取套餐列表失败，请重新登录")
    except Exception as e:
        logger.error(f"Plans error: {str(e)}")
        await update.message.reply_text("获取套餐列表失败：网络错误")


async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """获取订单列表"""
    if not await load_user_session(update):
        await update.message.reply_text("请先使用 /login 登录")
        return

    try:
        api = user_data[update.effective_user.id]['api']
        orders = api.get_order_list()
        if orders and 'data' in orders:
            orders = orders['data']
            message = "最近订单列表：\n\n"
            for order in orders[:5]:  # 只显示最近5个订单
                message += f"""
订单号：{order.get('trade_no', 'N/A')}
金额：{order.get('total_amount', 0)} 元
状态：{'已支付' if order.get('status') == 3 else '未支付'}
创建时间：{order.get('created_at', 'N/A')}
------------------------
"""
            await update.message.reply_text(message)
        else:
            # 如果获取信息失败，可能是认证过期
            await update.message.reply_text("获取订单列表失败，请重新登录")
    except Exception as e:
        logger.error(f"Orders error: {str(e)}")
        await update.message.reply_text("获取订单列表失败：网络错误")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消当前操作"""
    await update.message.reply_text("操作已取消")
    return ConversationHandler.END


async def create_emby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """创建Emby账号"""
    if not await load_user_session(update):
        await update.message.reply_text("请先使用 /login 登录V2Board账号")
        return

    # 检查是否已有Emby账号
    user_id = update.effective_user.id
    if user_data[user_id].get('emby'):
        await update.message.reply_text("您已经有Emby账号了，可以使用 /emby_info 查看账号信息")
        return

    try:
        # 获取用户信息，检查订阅等级
        api = user_data[user_id]['api']
        user_info = api.get_user_info()

        if not user_info or 'data' not in user_info:
            await update.message.reply_text("获取用户信息失败，请重新登录")
            return

        user_info = user_info['data']
        current_plan_id = user_info.get('plan_id')
        allowed_plan_ids = [int(x.strip()) for x in os.getenv(
            'ALLOWED_PLAN_IDS', '').split(',') if x.strip()]

        # 检查是否有订阅
        if not current_plan_id:
            await update.message.reply_text("您还没有订阅任何套餐，无法创建Emby账号")
            return

        # 检查订阅等级是否在允许列表中
        if current_plan_id not in allowed_plan_ids:
            await update.message.reply_text(f"您当前的订阅套餐不满足创建Emby账号的要求")
            return

        # 检查邮箱是否被其他Telegram账号使用
        email = user_data[user_id]['email']
        if not check_email_usage(email, user_id):
            await update.message.reply_text("该邮箱已被其他Telegram账号使用，无法创建Emby账号")
            return

        # 创建Emby账号
        emby = EmbyAPI()
        result = emby.create_user()

        if result["success"]:
            # 保存Emby账号信息
            user_data[user_id]['emby'] = {
                'username': result['username'],
                'password': result['password'],
                'user_id': result['user_id']
            }
            save_user_data(user_id, user_data[user_id])

            message = f"""
<b>Emby账号创建成功！</b>

用户名: <code>{result['username']}</code>
密码: <code>{result['password']}</code>
服务器地址: \n{os.getenv('EMBY_SERVER_URL_TEMPLATE')}

请妥善保管您的账号信息！
"""
            await update.message.reply_text(message, parse_mode='HTML')
        else:
            await update.message.reply_text(f"创建Emby账号失败: {result['error']}")
    except Exception as e:
        logger.error(f"Create Emby error: {str(e)}")
        await update.message.reply_text("创建Emby账号时发生错误")


async def emby_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看Emby账号信息"""
    if not await load_user_session(update):
        await update.message.reply_text("请先使用 /login 登录V2Board账号")
        return

    # 检查是否有Emby账号
    if not user_data[update.effective_user.id].get('emby'):
        await update.message.reply_text("您还没有Emby账号，请使用 /create_emby 创建")
        return

    emby_info = user_data[update.effective_user.id]['emby']
    message = f"""
<b>您的Emby账号信息：</b>

用户名: <code>{emby_info['username']}</code>
密码: <code>{emby_info['password']}</code>
服务器地址: \n{os.getenv('EMBY_SERVER_URL_TEMPLATE')}

请妥善保管您的账号信息！
"""
    await update.message.reply_text(message, parse_mode='HTML')


async def delete_emby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """删除Emby账号"""
    if not await load_user_session(update):
        await update.message.reply_text("请先使用 /login 登录")
        return

    # 检查是否有Emby账号
    user_id = update.effective_user.id
    if not user_data[user_id].get('emby'):
        await update.message.reply_text("您还没有Emby账号")
        return

    try:
        emby = EmbyAPI()
        emby_user_id = user_data[user_id]['emby']['user_id']
        result = emby.delete_user(emby_user_id)

        if result["success"]:
            # 从用户数据中删除Emby账号信息
            del user_data[user_id]['emby']
            save_user_data(user_id, user_data[user_id])
            await update.message.reply_text("您的Emby账号已成功删除")
            logger.info(
                f"用户 {user_data[user_id]['email']}(tg:{user_id}) 的Emby账号已成功删除")
        else:
            await update.message.reply_text(f"删除Emby账号失败: {result['error']}")
    except Exception as e:
        logger.error(f"Delete Emby error: {str(e)}")
        await update.message.reply_text("删除Emby账号时发生错误")


if __name__ == '__main__':
    """启动机器人"""
    # 加载邮箱映射数据
    load_email_map()

    # 创建应用
    application = Application.builder().token(TOKEN).build()

    # 创建登录会话处理器
    login_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login)],
        states={
            TYPING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, email_received)],
            TYPING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, password_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # 添加命令处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("info", info))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("plans", plans))
    application.add_handler(CommandHandler("orders", orders))
    application.add_handler(CommandHandler("create_emby", create_emby))
    application.add_handler(CommandHandler("emby_info", emby_info))
    application.add_handler(CommandHandler("delete_emby", delete_emby))
    application.add_handler(login_handler)

    # 添加定时任务
    application.job_queue.run_repeating(
        clean_expired_data, interval=600)  # 每10分钟清理过期数据

    # 添加订阅等级检查任务，每小时检查一次
    from scheduler import check_and_clean_invalid_emby_accounts
    application.job_queue.run_repeating(
        check_and_clean_invalid_emby_accounts,
        interval=3600,  # 每小时检查一次
        first=60  # 启动1分钟后开始第一次检查
    )

    # 启动机器人
    logger.info("机器人启动中...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
