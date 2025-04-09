from typing import List, Tuple, Dict, Any, Union, Optional
from datetime import datetime
from app.log import logger
from app.modules.transmission import Transmission
from transmission_rpc.torrent import Torrent
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.core.event import eventmanager, Event

class TransmissionTrackerCleaner(_PluginBase):
    """
    Transmission Tracker状态全面清理插件
    检测所有种子的Tracker状态，不论种子当前状态
    """

    # 插件元数据
    plugin_name = "Transmission失效种子清理"
    plugin_desc = "定时清理Transmission中Tracker失效的种子及文件"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    plugin_version = "1.8"
    plugin_author = "Aspeternity"
    author_url = "https://github.com/Aspeternity"
    plugin_config_prefix = "transmissiontrackercleaner_"
    plugin_order = 33
    auth_level = 1

    # 插件配置项
    _enabled: bool = False
    _onlyonce: bool = False
    _transmission: Transmission = None
    _host: str = None
    _port: int = None
    _username: str = None
    _password: str = None
    _delete_files: bool = True
    _dry_run: bool = True
    _tracker_patterns: List[str] = [
        "Torrent not exists",
        "not registered",
        "未注册",
        "unregistered",
        "not found",
        "torrent does not exist"
    ]
    _last_run_time: str = None

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        if config:
            self._enabled = config.get("enabled", False)
            self._onlyonce = config.get("onlyonce", False)
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            self._delete_files = config.get("delete_files", True)
            self._dry_run = config.get("dry_run", True)
            
            patterns_str = config.get("tracker_patterns", "")
            self._tracker_patterns = [p.strip() for p in patterns_str.split('\n') if p.strip()]
            
        if self._onlyonce:
            try:
                self._transmission = Transmission(
                    self._host, self._port, 
                    self._username, self._password
                )
                self._task()
                self._onlyonce = False
                self.__update_config()
            except Exception as e:
                logger.error(f"Transmission连接失败: {str(e)}")

    def _task(self):
        """执行清理任务，检查所有种子的Tracker状态"""
        if not self._transmission:
            logger.error("Transmission客户端未初始化")
            return
            
        logger.info("开始全面检测所有种子的Tracker状态...")
        self._last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 获取所有种子
        try:
            torrents, error = self._transmission.get_torrents()
            if error:
                logger.error("获取种子列表失败")
                return
        except Exception as e:
            logger.error(f"获取种子列表时出错: {str(e)}")
            return
            
        if not torrents:
            logger.info("没有找到任何种子")
            return
            
        deleted_count = 0
        matched_count = 0
        
        for torrent in torrents:
            # 获取种子基本信息
            torrent_id = getattr(torrent, "id", "")
            name = getattr(torrent, "name", "")
            status = getattr(torrent, "status", "unknown")
            
            # 获取Tracker状态信息
            tracker_stats = getattr(torrent, "trackerStats", [])
            tracker_messages = []
            
            # 收集所有Tracker消息
            for tracker in tracker_stats:
                if hasattr(tracker, "lastAnnounceResult"):
                    msg = getattr(tracker, "lastAnnounceResult", "")
                    if msg:
                        tracker_messages.append(msg.lower())
            
            # 检查Tracker消息是否匹配任何模式
            is_match = False
            for msg in tracker_messages:
                if any(pattern.lower() in msg for pattern in self._tracker_patterns):
                    is_match = True
                    break
                    
            if not is_match:
                continue
                
            matched_count += 1
            logger.info(
                f"发现Tracker异常的种子: {name} (ID: {torrent_id})\n"
                f"状态: {status}, Tracker消息: {tracker_messages}"
            )
            
            # 模拟运行模式
            if self._dry_run:
                logger.info(f"[模拟] 将删除种子: {name}")
                deleted_count += 1
                continue
                
            # 实际删除
            try:
                if self._transmission.delete_torrents(
                    delete_file=self._delete_files,
                    ids=torrent_id
                ):
                    logger.info(f"已删除种子: {name}")
                    deleted_count += 1
                else:
                    logger.error(f"删除种子失败: {name}")
            except Exception as e:
                logger.error(f"删除种子时出错: {name}, 错误: {str(e)}")
                
        # 输出统计信息
        logger.info("="*50)
        logger.info(f"检测完成: 共检查 {len(torrents)} 个种子")
        logger.info(f"发现Tracker异常的种子: {matched_count} 个")
        logger.info(f"处理种子: {deleted_count} 个")
        
        if matched_count == 0:
            logger.warning(
                "未发现Tracker异常的种子，可能原因:\n"
                "1. Tracker消息不匹配当前配置的模式\n"
                "2. 确实没有Tracker异常的种子\n"
                f"当前配置的匹配模式: {self._tracker_patterns}\n"
                "请检查日志中的Tracker原始消息"
            )
        
        self.__update_config()

    def __update_config(self):
        """更新配置"""
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "password": self._password,
            "delete_files": self._delete_files,
            "dry_run": self._dry_run,
            "tracker_patterns": "\n".join(self._tracker_patterns),
            "last_run_time": self._last_run_time
        })

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """获取配置表单"""
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'dry_run',
                                            'label': '模拟运行',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'delete_files',
                                            'label': '删除文件',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'host',
                                            'label': 'Transmission主机',
                                            'placeholder': '192.168.1.100'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'port',
                                            'label': '端口',
                                            'placeholder': '9091'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'username',
                                            'label': '用户名',
                                            'placeholder': 'admin'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'password',
                                            'label': '密码',
                                            'placeholder': 'password'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'tracker_patterns',
                                            'label': 'Tracker错误匹配模式(每行一个)',
                                            'placeholder': 'Torrent not exists\n未注册\ntorrent does not exist',
                                            'rows': 3,
                                            'auto-grow': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
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
                                            'text': '插件功能说明:\n'
                                                    '1. 检测所有种子的Tracker状态，不论种子当前状态\n'
                                                    '2. 常见Tracker错误模式:\n'
                                                    '   - Torrent not exists\n'
                                                    '   - not registered\n'
                                                    '   - 未注册\n'
                                                    '   - torrent does not exist\n'
                                                    '3. 建议首次使用时启用"模拟运行"模式',
                                            'style': 'white-space: pre-line;'
                                        }
                                    },
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': f'上次运行时间: {self._last_run_time or "从未运行"}',
                                            'style': 'white-space: pre-line;'
                                        }
                                    },
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'warning',
                                            'variant': 'tonal',
                                            'text': '重要提示:\n'
                                                    '1. 删除操作不可逆，请谨慎使用\n'
                                                    '2. 如果检测不到种子，请检查Tracker消息是否匹配\n'
                                                    '3. 可以添加更多匹配模式以提高检测率',
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
            "enabled": False,
            "onlyonce": False,
            "dry_run": True,
            "delete_files": True,
            "host": "192.168.1.100",
            "port": 9091,
            "username": "admin",
            "password": "password",
            "tracker_patterns": "Torrent not exists\n未注册\ntorrent does not exist",
            "last_run_time": None
        }

    def get_page(self) -> List[dict]:
        """获取插件页面"""
        return [
            {
                'component': 'div',
                'text': f'<p>上次运行时间: {self._last_run_time or "从未运行"}</p>',
                'props': {
                    'class': 'text-center'
                }
            },
            {
                'component': 'div',
                'text': '<p>提示：查看系统日志获取详细执行情况</p>',
                'props': {
                    'class': 'text-center text-gray-500'
                }
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """获取API"""
        pass

    def get_command(self) -> List[Dict[str, Any]]:
        """获取命令"""
        pass
