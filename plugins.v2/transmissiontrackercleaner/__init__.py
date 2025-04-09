from typing import List, Tuple, Dict, Any, Union
from app.log import logger
from app.modules.transmission import Transmission
from transmission_rpc.torrent import Torrent
from app.plugins import _PluginBase
from apscheduler.schedulers.background import BackgroundScheduler
import os

class TransmissionTrackerCleaner(_PluginBase):
    """
    Transmission失效种子清理插件
    功能：定时清理Transmission中Tracker返回特定错误信息的种子及关联文件
    """
    
    # ==================== 插件元数据 ====================
    plugin_name = "Transmission失效种子清理"
    plugin_desc = "定时清理Transmission中Tracker失效的种子及文件"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/delete.png"
    plugin_version = "1.1"  # 版本更新
    plugin_author = "Aspeternity"
    author_url = "https://github.com/Aspeternity"
    plugin_config_prefix = "transmissiontrackercleaner_"
    plugin_order = 32
    auth_level = 1

    # ==================== 初始化配置 ====================
    def __init__(self):
        super().__init__()
        # 插件配置项
        self._enabled = False  # 是否启用插件
        self._cron = ""  # 定时任务cron表达式
        self._onlyonce = False  # 是否立即运行一次
        self._enable_periodic = False  # 是否启用周期性巡检
        self._transmission = None  # Transmission客户端实例
        self._host = None  # Transmission主机地址
        self._port = None  # Transmission端口
        self._username = None  # 用户名
        self._password = None  # 密码
        # 需要匹配的Tracker错误信息列表（默认值）
        self._tracker_errors = [
            "torrent not exists", 
            "unregistered torrent",
            "torrent not registered",
            "not registered"
        ]
        self._delete_files = True  # 是否删除文件
        self._dry_run = True  # 是否模拟运行（不实际删除）
        self._scheduler = None  # 定时任务调度器实例

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        :param config: 插件配置字典
        """
        if config:
            # 从配置中加载各项参数
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron", "")
            self._onlyonce = config.get("onlyonce", False)
            self._enable_periodic = config.get("enable_periodic", False)  # 新增周期性巡检开关
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            # 处理Tracker错误配置（按行分割并去除空行和前后空格）
            self._tracker_errors = [x.strip().lower() for x in config.get("tracker_errors", "").split("\n") if x.strip()]
            self._delete_files = config.get("delete_files", True)
            self._dry_run = config.get("dry_run", True)

        # 停止现有服务（避免重复初始化）
        self.stop_service()

        # 如果插件启用或设置了立即运行
        if self._enabled or self._onlyonce:
            try:
                # 初始化Transmission客户端
                self._transmission = Transmission(self._host, self._port, self._username, self._password)
            except Exception as e:
                logger.error(f"初始化Transmission连接失败: {str(e)}")
                return

            # 设置定时任务（仅在启用周期性巡检时）
            if self._enable_periodic and self._cron:
                self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
                # 将cron表达式解析为调度器参数
                self._scheduler.add_job(self._task, 'cron', **self._parse_cron(self._cron))
                self._scheduler.start()
                logger.info(f"定时服务启动，执行周期: {self._cron}")
            elif self._enable_periodic and not self._cron:
                logger.warning("已启用周期性巡检但未设置cron表达式，定时任务将不会运行")

            # 立即运行一次
            if self._onlyonce:
                self._task()
                self._onlyonce = False
                self.__update_config()  # 更新配置（主要是重置onlyonce标志）

    def _parse_cron(self, cron_str: str) -> dict:
        """
        解析cron表达式为APScheduler参数
        :param cron_str: cron表达式字符串（如 "0 3 * * *"）
        :return: 解析后的参数字典
        """
        parts = cron_str.split()
        if len(parts) != 5:  # 如果不是标准的5部分cron表达式
            return {"second": "0"}  # 默认每分钟运行
        return {
            "minute": parts[0],
            "hour": parts[1],
            "day": parts[2],
            "month": parts[3],
            "day_of_week": parts[4]
        }

    def _task(self):
        """
        主任务逻辑：检查并清理失效种子
        """
        if not self._transmission:
            logger.error("Transmission客户端未初始化")
            return

        # 获取所有种子
        torrents, error = self._transmission.get_torrents()
        if error:
            logger.error(f"获取种子列表失败: {error}")
            return

        # 找出需要删除的种子
        to_remove = []
        for torrent in torrents:
            try:
                # 检查tracker信息
                if not hasattr(torrent, 'trackers') or not torrent.trackers:
                    continue

                # 检查每个Tracker的返回信息
                for tracker in torrent.trackers:
                    # 获取tracker最后返回信息（兼容不同字段名）
                    last_announce = tracker.get('lastAnnounceResult') or tracker.get('last_announce_result') or ""
                    if not last_announce:
                        continue
                        
                    # 检查是否包含配置的错误信息
                    tracker_msg = last_announce.lower()
                    if any(error_msg in tracker_msg for error_msg in self._tracker_errors):
                        to_remove.append(torrent)
                        logger.info(f"发现失效种子: {torrent.name} (Tracker错误: {last_announce})")
                        break  # 找到一个错误就足够

            except Exception as e:
                logger.warning(f"检查种子失败: {torrent.name}, 错误: {str(e)}")
                continue

        if not to_remove:
            logger.info("没有找到失效种子")
            return

        # 处理需要删除的种子
        logger.info(f"找到 {len(to_remove)} 个失效种子")
        removed_count = 0
        removed_size = 0

        for torrent in to_remove:
            try:
                size = torrent.total_size  # 获取种子大小
                
                if not self._dry_run:  # 非模拟模式才实际删除
                    if self._delete_files:  # 根据配置决定是否删除文件
                        self._transmission.delete_torrents(delete_file=True, ids=[torrent.hashString])
                        logger.info(f"已删除种子及文件: {torrent.name}")
                    else:
                        self._transmission.delete_torrents(delete_file=False, ids=[torrent.hashString])
                        logger.info(f"已删除种子(保留文件): {torrent.name}")
                    
                    removed_count += 1
                    removed_size += size
                else:  # 模拟模式只记录日志
                    logger.info(f"[模拟] 将删除种子: {torrent.name} (大小: {self._format_size(size)})")

            except Exception as e:
                logger.error(f"删除种子失败 {torrent.name}: {str(e)}")

        # 输出结果摘要
        if not self._dry_run:
            logger.info(f"清理完成，共删除 {removed_count} 个种子，释放空间: {self._format_size(removed_size)}")
        else:
            logger.info(f"[模拟] 共发现 {len(to_remove)} 个待清理种子")

    def _format_size(self, size_bytes):
        """
        将字节数转换为易读的格式（如KB、MB、GB）
        :param size_bytes: 字节数
        :return: 格式化后的字符串
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"

    def __update_config(self):
        """更新插件配置"""
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "enable_periodic": self._enable_periodic,  # 新增配置项
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "password": self._password,
            "tracker_errors": "\n".join(self._tracker_errors),  # 将列表转换为换行分隔的字符串
            "delete_files": self._delete_files,
            "dry_run": self._dry_run
        })

    # ==================== 插件接口方法 ====================
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """获取插件命令（暂未实现）"""
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """获取API（暂未实现）"""
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        获取插件配置表单
        :return: (表单组件列表, 表单默认值字典)
        """
        return [
            # 表单布局结构
            {
                'component': 'VForm',
                'content': [
                    # 第一行：开关按钮
                    {
                        'component': 'VRow',
                        'content': [
                            # 启用插件开关
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}]
                            },
                            # 立即运行一次开关
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{'component': 'VSwitch', 'props': {'model': 'onlyonce', 'label': '立即运行一次'}}]
                            },
                            # 启用周期性巡检开关（新增）
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{'component': 'VSwitch', 'props': {
                                    'model': 'enable_periodic', 
                                    'label': '启用周期性巡检',
                                    'hint': '开启时务必填写cron表达式'
                                }}]
                            },
                            # 模拟运行开关
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{'component': 'VSwitch', 'props': {'model': 'dry_run', 'label': '模拟运行'}}]
                            }
                        ]
                    },
                    # 第二行：删除选项
                    {
                        'component': 'VRow',
                        'content': [
                            # 删除文件开关
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 4},
                                'content': [{'component': 'VSwitch', 'props': {'model': 'delete_files', 'label': '删除文件'}}]
                            }
                        ]
                    },
                    # 第三行：Transmission连接配置
                    {
                        'component': 'VRow',
                        'content': [
                            # 主机地址输入框
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {
                                    'model': 'host', 
                                    'label': 'Transmission主机IP',
                                    'placeholder': '192.168.1.100'
                                }}]
                            },
                            # 端口输入框
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {
                                    'model': 'port', 
                                    'label': 'Transmission端口',
                                    'placeholder': '9091'
                                }}]
                            }
                        ]
                    },
                    # 第四行：认证信息
                    {
                        'component': 'VRow',
                        'content': [
                            # 用户名输入框
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {
                                    'model': 'username', 
                                    'label': '用户名',
                                    'placeholder': 'admin'
                                }}]
                            },
                            # 密码输入框
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {
                                    'model': 'password', 
                                    'label': '密码',
                                    'placeholder': 'password'
                                }}]
                            }
                        ]
                    },
                    # 第五行：定时任务配置
                    {
                        'component': 'VRow',
                        'content': [
                            # cron表达式输入框
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {
                                    'model': 'cron', 
                                    'label': '定时清理周期(cron表达式)',
                                    'placeholder': '0 3 * * *',
                                    'hint': '开启周期性巡检时必填'
                                }}]
                            }
                        ]
                    },
                    # 第六行：Tracker错误配置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{'component': 'VTextarea', 'props': {
                                    'model': 'tracker_errors', 
                                    'label': 'Tracker错误信息(每行一个)',
                                    'placeholder': 'torrent not exists\nunregistered torrent',
                                    'rows': 3
                                }}]
                            }
                        ]
                    },
                    # 第七行：说明信息
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    # 信息提示
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '本插件会检查Transmission中的种子，清理Tracker返回特定错误信息的种子\n'
                                                    '常见PT站删除种子错误信息: "torrent not exists", "unregistered torrent"\n'
                                                    '建议首次使用时启用"模拟运行"模式，确认无误后再关闭模拟模式\n'
                                                    '定时任务使用cron表达式，例如: "0 3 * * *"表示每天凌晨3点执行\n'
                                                    '开启"周期性巡检"时才会启用定时任务',
                                            'style': 'white-space: pre-line;'
                                        }
                                    },
                                    # 警告信息
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'warning',
                                            'variant': 'tonal',
                                            'text': '警告：种子和文件删除操作不可逆，请谨慎操作！\n'
                                                    '开启周期性巡检时务必填写正确的cron表达式',
                                            'style': 'white-space: pre-line;'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            # 表单默认值
            "enabled": False,
            "cron": "0 3 * * *",  # 默认每天凌晨3点运行
            "onlyonce": False,
            "enable_periodic": False,  # 默认关闭周期性巡检
            "delete_files": True,
            "dry_run": True,
            "host": "192.168.1.100",
            "port": 9091,
            "username": "admin",
            "password": "password",
            "tracker_errors": "torrent not exists\nunregistered torrent"  # 默认错误匹配
        }

    def get_page(self) -> List[dict]:
        """获取插件页面（暂未实现）"""
        pass

    def get_state(self) -> bool:
        """获取插件状态"""
        return self._enabled

    def stop_service(self):
        """停止插件服务"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()  # 移除所有任务
                if self._scheduler.running:
                    self._scheduler.shutdown()  # 关闭调度器
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止定时任务失败: {str(e)}")
