from typing import List, Tuple, Dict, Any, Union
from app.log import logger
from app.modules.transmission import Transmission
from transmission_rpc.torrent import Torrent
from app.plugins import _PluginBase
from apscheduler.schedulers.background import BackgroundScheduler
import re

class TransmissionTrackerCleaner(_PluginBase):
    """
    Transmission失效种子清理插件（增强版）
    功能：通过三重检测机制清理失效种子：
    1. 种子errorString字段检测
    2. Tracker返回消息检测
    3. 错误状态+0分享率组合检测
    """

    # ==================== 插件元数据 ====================
    plugin_name = "Transmission失效种子清理"
    plugin_desc = "定时清理Transmission中Tracker失效的种子及文件"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/delete.png"
    plugin_version = "1.2"
    plugin_author = "Aspeternity"
    author_url = "https://github.com/Aspeternity"
    plugin_config_prefix = "transmissiontrackercleaner_"
    plugin_order = 32
    auth_level = 1

    # ==================== 初始化配置 ====================
    def __init__(self):
        super().__init__()
        # 基础配置
        self._enabled = False
        self._cron = ""
        self._onlyonce = False
        self._enable_periodic = False
        self._transmission = None
        self._host = None
        self._port = None
        self._username = None
        self._password = None
        
        # 错误检测配置
        self._tracker_errors = [
            "torrent not exists",
            "unregistered torrent",
            "torrent not registered",
            "not registered",
            "torrent does not exist",
            "this torrent does not exist",
            "could not find torrent",
            "invalid info hash",
            "torrent not found",
            "未注册的种子",
            "该种子未注册"
        ]
        
        # 操作配置
        self._delete_files = True
        self._dry_run = True
        self._debug_mode = False
        self._scheduler = None

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        if config:
            # 基础配置
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron", "")
            self._onlyonce = config.get("onlyonce", False)
            self._enable_periodic = config.get("enable_periodic", False)
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            self._debug_mode = config.get("debug_mode", False)
            
            # 合并错误配置
            custom_errors = [
                x.strip().lower() 
                for x in config.get("tracker_errors", "").split("\n") 
                if x.strip()
            ]
            self._tracker_errors = list(set(self._tracker_errors + custom_errors))
            
            # 操作配置
            self._delete_files = config.get("delete_files", True)
            self._dry_run = config.get("dry_run", True)

        # 停止现有服务
        self.stop_service()

        if self._enabled or self._onlyonce:
            try:
                self._transmission = Transmission(
                    host=self._host,
                    port=self._port,
                    username=self._username,
                    password=self._password
                )
            except Exception as e:
                logger.error(f"Transmission连接失败: {str(e)}")
                return

            # 定时任务设置
            if self._enable_periodic:
                if not self._cron:
                    logger.warning("已启用周期性巡检但未设置cron表达式")
                else:
                    self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
                    self._scheduler.add_job(
                        self._task,
                        'cron',
                        **self._parse_cron(self._cron)
                    )
                    self._scheduler.start()
                    logger.info(f"定时任务启动，执行周期: {self._cron}")

            # 立即执行一次
            if self._onlyonce:
                self._task()
                self._onlyonce = False
                self.__update_config()

    def _parse_cron(self, cron_str: str) -> dict:
        """解析cron表达式"""
        parts = cron_str.split()
        if len(parts) != 5:
            return {"second": "0"}
        return {
            "minute": parts[0],
            "hour": parts[1],
            "day": parts[2],
            "month": parts[3],
            "day_of_week": parts[4]
        }

    def _task(self):
        """主任务执行入口"""
        if not self._transmission:
            logger.error("Transmission客户端未初始化")
            return

        # 获取种子列表
        torrents, error = self._transmission.get_torrents()
        if error:
            logger.error("获取种子列表失败")
            return

        # 检测失效种子
        to_remove = self._check_invalid_torrents(torrents)
        
        # 处理失效种子
        self._process_invalid_torrents(to_remove)

    def _check_invalid_torrents(self, torrents: List[Torrent]) -> List[Torrent]:
        """
        三重检测机制发现失效种子
        返回: 需要删除的种子列表
        """
        to_remove = []
        
        for torrent in torrents:
            try:
                # 调试信息
                self._log_debug_info(torrent)
                
                # 检测维度1：种子错误状态
                if self._check_by_error_string(torrent):
                    to_remove.append(torrent)
                    continue
                    
                # 检测维度2：Tracker返回消息
                if self._check_by_tracker_messages(torrent):
                    to_remove.append(torrent)
                    continue
                    
                # 检测维度3：错误状态+0分享率
                if self._check_by_error_ratio(torrent):
                    to_remove.append(torrent)
                    
            except Exception as e:
                logger.warning(f"检查种子失败 {getattr(torrent, 'name', '未知')}: {str(e)}")
                
        return to_remove

    def _log_debug_info(self, torrent: Torrent):
        """记录调试信息"""
        if not self._debug_mode:
            return
            
        logger.debug(f"\n{'='*30}")
        logger.debug(f"检查种子: {getattr(torrent, 'name', '未知名称')}")
        logger.debug(f"状态: {getattr(torrent, 'status', '未知状态')}")
        logger.debug(f"错误码: {getattr(torrent, 'error', '无')}")
        logger.debug(f"错误信息: {getattr(torrent, 'errorString', '无')}")
        logger.debug(f"分享率: {getattr(torrent, 'uploadRatio', '无')}")
        
        if hasattr(torrent, 'trackers') and torrent.trackers:
            for i, tracker in enumerate(torrent.trackers[:3]):  # 只显示前3个tracker
                logger.debug(f"Tracker{i+1}: {tracker.get('announce', '未知地址')}")
                logger.debug(f"最后消息: {tracker.get('lastAnnounceResult', '无')}")

    def _check_by_error_string(self, torrent: Torrent) -> bool:
        """通过errorString字段检测"""
        if not hasattr(torrent, 'errorString') or not torrent.errorString:
            return False
            
        error_msg = torrent.errorString.lower()
        clean_msg = re.sub(r'[^\w\s]', '', error_msg)  # 移除标点符号
        
        for err in self._tracker_errors:
            if err in clean_msg:
                logger.info(f"[错误状态] 发现失效种子: {torrent.name} | 错误: {torrent.errorString}")
                return True
        return False

    def _check_by_tracker_messages(self, torrent: Torrent) -> bool:
        """通过Tracker消息检测"""
        if not hasattr(torrent, 'trackers') or not torrent.trackers:
            return False

        for tracker in torrent.trackers:
            # 兼容不同版本字段名
            msg = (tracker.get('lastAnnounceResult') or 
                  tracker.get('last_announce_result') or 
                  tracker.get('announceResult') or "")
                  
            if not msg:
                continue
                
            # 标准化处理
            clean_msg = re.sub(r'[^\w\s]', '', msg.lower())
            
            for err in self._tracker_errors:
                if err in clean_msg:
                    logger.info(f"[Tracker消息] 发现失效种子: {torrent.name} | 消息: {msg}")
                    return True
        return False

    def _check_by_error_ratio(self, torrent: Torrent) -> bool:
        """通过错误状态+0分享率检测"""
        return (
            hasattr(torrent, 'status') and 
            torrent.status == 'error' and 
            hasattr(torrent, 'uploadRatio') and 
            torrent.uploadRatio == 0
        )

    def _process_invalid_torrents(self, to_remove: List[Torrent]):
        """处理失效种子"""
        if not to_remove:
            logger.info("✅ 未检测到失效种子")
            return

        logger.info(f"⚠️ 发现 {len(to_remove)} 个失效种子")
        success_count = 0

        for torrent in to_remove:
            try:
                torrent_name = getattr(torrent, 'name', '未知种子')
                
                if self._dry_run:
                    logger.info(f"[模拟删除] {torrent_name}")
                    continue
                    
                # 实际删除操作
                deleted = self._transmission.delete_torrents(
                    delete_file=self._delete_files,
                    ids=[torrent.hashString]
                )
                
                if deleted:
                    success_count += 1
                    logger.info(f"🗑️ 已删除: {torrent_name}")
                else:
                    logger.error(f"❌ 删除失败: {torrent_name}")
                    
            except Exception as e:
                logger.error(f"❌ 删除异常 {torrent_name}: {str(e)}")

        # 结果汇总
        if not self._dry_run:
            logger.info(f"💯 清理完成，成功删除 {success_count}/{len(to_remove)} 个种子")

    def __update_config(self):
        """更新插件配置"""
        self.update_config({
            # 基础配置
            "enabled": self._enabled,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "enable_periodic": self._enable_periodic,
            "debug_mode": self._debug_mode,
            # 连接配置
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "password": self._password,
            # 操作配置
            "tracker_errors": "\n".join(self._tracker_errors),
            "delete_files": self._delete_files,
            "dry_run": self._dry_run
        })

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """获取配置表单"""
        return [
            {
                'component': 'VForm',
                'content': [
                    # 第一行：功能开关
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'enabled',
                                        'label': '启用插件',
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'debug_mode',
                                        'label': '调试模式',
                                        'hint': '显示详细检测日志'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'enable_periodic',
                                        'label': '启用周期性巡检',
                                        'hint': '需配合cron表达式使用'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'dry_run',
                                        'label': '模拟运行',
                                        'hint': '只记录不实际删除'
                                    }
                                }]
                            }
                        ]
                    },
                    
                    # 第二行：连接配置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'host',
                                        'label': 'Transmission地址',
                                        'placeholder': '192.168.1.100'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'port',
                                        'label': '端口',
                                        'placeholder': '9091'
                                    }
                                }]
                            }
                        ]
                    },
                    
                    # 第三行：认证信息
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'username',
                                        'label': '用户名',
                                        'placeholder': 'admin'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'password',
                                        'label': '密码',
                                        'type': 'password',
                                        'placeholder': 'password'
                                    }
                                }]
                            }
                        ]
                    },
                    
                    # 第四行：定时设置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'cron',
                                        'label': '定时周期(cron)',
                                        'placeholder': '0 3 * * *',
                                        'hint': '启用周期性巡检时必填'
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'delete_files',
                                        'label': '删除文件',
                                        'hint': '是否同时删除文件'
                                    }
                                }]
                            }
                        ]
                    },
                    
                    # 第五行：错误配置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{
                                    'component': 'VTextarea',
                                    'props': {
                                        'model': 'tracker_errors',
                                        'label': '错误关键词(每行一个)',
                                        'rows': 3,
                                        'placeholder': 'unregistered torrent\ntorrent not exists'
                                    }
                                }]
                            }
                        ]
                    },
                    
                    # 第六行：操作按钮
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VBtn',
                                    'props': {
                                        'block': True,
                                        'variant': 'tonal',
                                        'prepend-icon': 'mdi-cached',
                                        'text': '立即运行一次',
                                        'click': 'onlyonce=true'
                                    }
                                }]
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
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '🔍 三重检测机制说明：\n'
                                                   '1. 种子errorString字段检测\n'
                                                   '2. Tracker返回消息检测\n'
                                                   '3. 错误状态+0分享率组合检测\n\n'
                                                   '💡 首次使用建议开启调试模式和模拟运行',
                                            'style': 'white-space: pre-line;'
                                        }
                                    },
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'warning',
                                            'variant': 'tonal',
                                            'text': '⚠️ 警告：\n'
                                                   '• 文件删除操作不可逆\n'
                                                   '• 启用周期性巡检需设置cron表达式\n'
                                                   '• 实际删除前请确认模拟运行结果',
                                            'style': 'white-space: pre-line;'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                # 默认值
                "enabled": False,
                "debug_mode": False,
                "enable_periodic": False,
                "dry_run": True,
                "delete_files": True,
                "host": "192.168.1.100",
                "port": 9091,
                "username": "admin",
                "password": "password",
                "cron": "0 3 * * *",
                "tracker_errors": "unregistered torrent\ntorrent not exists"
            }
        ]

    def stop_service(self):
        """停止插件服务"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止定时任务失败: {str(e)}")

    # 保持其他必要接口方法
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        return []

    def get_state(self) -> bool:
        return self._enabled
