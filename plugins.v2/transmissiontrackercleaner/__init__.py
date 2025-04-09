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
    Transmission错误种子清理插件
    功能：检查并删除Transmission中处于错误状态的种子及其文件
    特点：
    1. 支持模拟运行模式（只记录不实际删除）
    2. 支持立即执行一次
    3. 可配置错误信息匹配模式
    4. 可选择是否删除文件
    """

    # 插件元数据
    plugin_name = "Transmission失效种子清理"
    plugin_desc = "定时清理Transmission中Tracker失效的种子及文件"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    plugin_version = "1.5"
    plugin_author = "Aspeternity"
    author_url = "https://github.com/Aspeternity"
    plugin_config_prefix = "transmissiontrackercleaner_"
    plugin_order = 32
    auth_level = 1

    # 插件配置项
    _enabled: bool = False                # 是否启用插件
    _onlyonce: bool = False               # 是否立即运行一次
    _transmission: Transmission = None    # Transmission客户端实例
    _host: str = None                     # Transmission主机地址
    _port: int = None                     # Transmission端口
    _username: str = None                 # 用户名
    _password: str = None                 # 密码
    _delete_files: bool = True            # 是否删除文件
    _dry_run: bool = True                 # 是否模拟运行（只记录不删除）
    _error_patterns: List[str] = [        # 错误信息匹配模式
        "Torrent not exists", 
        "not registered", 
        "未注册"
    ]
    _last_run_time: str = None            # 上次运行时间

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        :param config: 插件配置
        """
        if config:
            # 从配置中加载各参数
            self._enabled = config.get("enabled", False)
            self._onlyonce = config.get("onlyonce", False)
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            self._delete_files = config.get("delete_files", True)
            self._dry_run = config.get("dry_run", True)
            
            # 处理错误匹配模式（多行文本转换为列表）
            error_patterns_str = config.get("error_patterns", "")
            self._error_patterns = [p.strip() for p in error_patterns_str.split('\n') if p.strip()]
            
        # 如果设置了立即运行一次，则执行任务
        if self._onlyonce:
            try:
                # 初始化Transmission客户端
                self._transmission = Transmission(
                    self._host, 
                    self._port, 
                    self._username, 
                    self._password
                )
                # 执行清理任务
                self._task()
                # 重置立即运行标志
                self._onlyonce = False
                # 更新配置
                self.__update_config()
            except Exception as e:
                logger.error(f"初始化Transmission连接失败: {str(e)}")

    def _task(self):
        """
        执行清理任务
        """
        # 检查Transmission客户端是否初始化
        if not self._transmission:
            logger.error("Transmission客户端未初始化")
            return
            
        logger.info("开始检查Transmission中的错误种子...")
        # 记录本次运行时间
        self._last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 获取所有种子
        torrents, error = self._transmission.get_torrents()
        if error:
            logger.error(f"获取种子列表失败: {error}")
            return
            
        deleted_count = 0  # 删除计数器
        
        # 检查每个种子
        for torrent in torrents:
            # 跳过没有错误的种子
            if not torrent.error or torrent.error == 0:
                continue
                
            # 获取错误信息
            error_str = getattr(torrent, "errorString", "") or ""
            
            # 检查错误信息是否匹配任何模式
            if not any(pattern.lower() in error_str.lower() for pattern in self._error_patterns):
                continue
                
            # 记录发现的错误种子
            logger.info(f"发现错误种子: {torrent.name} (ID: {torrent.id}), 错误信息: {error_str}")
            
            # 如果是模拟运行模式，只记录不删除
            if self._dry_run:
                logger.info(f"模拟模式: 将删除种子: {torrent.name} (ID: {torrent.id})")
                deleted_count += 1
                continue
                
            # 实际删除种子
            try:
                if self._transmission.delete_torrents(
                    delete_file=self._delete_files, 
                    ids=torrent.id
                ):
                    logger.info(f"已删除种子: {torrent.name} (ID: {torrent.id})")
                    deleted_count += 1
                else:
                    logger.error(f"删除种子失败: {torrent.name} (ID: {torrent.id})")
            except Exception as e:
                logger.error(f"删除种子时出错: {torrent.name} (ID: {torrent.id}), 错误: {str(e)}")
                
        # 输出结果摘要
        if deleted_count == 0:
            logger.info("没有发现需要删除的错误种子")
        else:
            if self._dry_run:
                logger.info(f"模拟运行完成，共发现 {deleted_count} 个需要删除的错误种子")
            else:
                logger.info(f"删除完成，共删除 {deleted_count} 个错误种子")
            
        # 更新配置
        self.__update_config()

    def __update_config(self):
        """
        更新插件配置
        """
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": self._onlyonce,
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "password": self._password,
            "delete_files": self._delete_files,
            "dry_run": self._dry_run,
            "error_patterns": "\n".join(self._error_patterns),
            "last_run_time": self._last_run_time
        })

    def get_state(self) -> bool:
        """
        获取插件状态
        """
        return self._enabled

    def stop_service(self):
        """
        停止插件服务
        """
        # 此版本无周期任务，无需特殊处理
        pass

    @eventmanager.register(EventType.PluginAction)
    def handle_manual_clean(self, event: Event):
        """
        处理手动清理事件
        """
        if event:
            event_data = event.event_data
            # 检查是否为当前插件的动作
            if not event_data or event_data.get("action") != "transmission_error_clean":
                return
            logger.info("收到手动清理错误种子命令，开始执行...")
            self._task()

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        获取插件配置表单
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
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
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
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
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'dry_run',
                                            'label': '模拟运行',
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
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'host',
                                            'label': 'Transmission主机IP',
                                            'placeholder': '192.168.1.100'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'port',
                                            'label': 'Transmission端口',
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
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
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
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
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
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'delete_files',
                                            'label': '删除文件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'error_patterns',
                                            'label': '错误信息匹配模式(每行一个)',
                                            'placeholder': 'Torrent not exists\n未注册',
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
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '功能说明：\n'
                                                    '1. 检查Transmission中的种子状态，删除匹配错误信息的种子\n'
                                                    '2. 错误信息匹配模式支持多行，每行一个匹配关键词(不区分大小写)\n'
                                                    '3. "模拟运行"模式下只记录不实际删除\n'
                                                    '4. "删除文件"选项控制是否同时删除文件',
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
                                            'text': '警告：\n'
                                                    '1. 文件删除操作不可逆，请谨慎操作！\n'
                                                    '2. 建议首次使用时启用"模拟运行"模式',
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
            "error_patterns": "Torrent not exists\n未注册",
            "last_run_time": None
        }

    def get_page(self) -> List[dict]:
        """
        获取插件页面
        """
        return [
            {
                'component': 'div',
                'text': f'<p>上次运行时间: {self._last_run_time or "从未运行"}</p>',
                'props': {
                    'class': 'text-center'
                }
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取API
        """
        pass

    def get_command(self) -> List[Dict[str, Any]]:
        """
        获取命令
        """
        pass
