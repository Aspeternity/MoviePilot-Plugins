from typing import List, Tuple, Dict, Any, Union, Optional
from datetime import datetime, timedelta
from app.log import logger
from app.modules.transmission import Transmission
from transmission_rpc.torrent import Torrent
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.core.event import eventmanager, Event
import time
import re

class TransmissionTrackerCleaner(_PluginBase):
    # Plugin metadata
    plugin_name = "Transmission失效种子清理"
    plugin_desc = "定时清理Transmission中Tracker失效的种子及文件"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    plugin_version = "1.3"
    plugin_author = "Aspeternity"
    author_url = "https://github.com/Aspeternity"
    plugin_config_prefix = "transmissiontrackercleaner_"
    plugin_order = 32
    auth_level = 1

    # Plugin configuration
    _enabled: bool = False
    _cron: str = "12h"
    _onlyonce: bool = False
    _transmission: Transmission = None
    _host: str = None
    _port: int = None
    _username: str = None
    _password: str = None
    _delete_files: bool = True
    _error_patterns: List[str] = ["Torrent not exists", "not registered", "未注册"]
    _last_run_time: str = None

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron", "12h")
            self._onlyonce = config.get("onlyonce", False)
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            self._delete_files = config.get("delete_files", True)
            error_patterns_str = config.get("error_patterns", "")
            self._error_patterns = [p.strip() for p in error_patterns_str.split('\n') if p.strip()]
            
        # Stop existing tasks
        self.stop_service()
        
        # Start new task if enabled
        if self._enabled or self._onlyonce:
            self._transmission = Transmission(self._host, self._port, self._username, self._password)
            
            # Run immediately if onlyonce
            if self._onlyonce:
                logger.info("Transmission错误种子清理服务启动，立即运行一次")
                self._scheduler.add_job(self._task, 'date',
                                       run_date=datetime.now() + timedelta(seconds=3),
                                       name="Transmission错误种子清理")
                # Close onlyonce flag
                self._onlyonce = False
                self.__update_config()
                
            # Start scheduled task
            if self._enabled:
                try:
                    # Parse cron expression
                    cron = self._parse_cron(self._cron)
                    if cron:
                        self._scheduler.add_job(self._task, 'interval', **cron,
                                              name="Transmission错误种子清理")
                        logger.info(f"Transmission错误种子清理服务启动，周期：{self._cron}")
                except Exception as e:
                    logger.error(f"定时任务配置错误：{str(e)}")
                    self._enabled = False
                    self.__update_config()

    def _parse_cron(self, cron_str: str) -> Optional[Dict[str, Any]]:
        """
        Parse cron expression like "12h" or "30m" into scheduler kwargs
        """
        if not cron_str:
            return None
            
        match = re.match(r'^(\d+)([mhd])$', cron_str.lower())
        if not match:
            return None
            
        value, unit = match.groups()
        value = int(value)
        
        if unit == 'm':
            return {'minutes': value}
        elif unit == 'h':
            return {'hours': value}
        elif unit == 'd':
            return {'days': value}
        return None

    def _task(self):
        if not self._transmission:
            logger.error("Transmission客户端未初始化")
            return
            
        logger.info("开始检查Transmission中的错误种子...")
        self._last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Get all torrents from Transmission
        torrents, error = self._transmission.get_torrents()
        if error:
            logger.error(f"获取种子列表失败: {error}")
            return
            
        # Check each torrent for errors
        deleted_count = 0
        for torrent in torrents:
            if not torrent.error or torrent.error == 0:
                continue
                
            # Check error string against patterns
            error_str = getattr(torrent, "errorString", "") or ""
            if not any(pattern.lower() in error_str.lower() for pattern in self._error_patterns):
                continue
                
            # Log the torrent to be deleted
            logger.info(f"发现错误种子: {torrent.name} (ID: {torrent.id}), 错误信息: {error_str}")
            
            # Delete the torrent
            try:
                if self._transmission.delete_torrents(delete_file=self._delete_files, ids=torrent.id):
                    logger.info(f"已删除种子: {torrent.name} (ID: {torrent.id})")
                    deleted_count += 1
                else:
                    logger.error(f"删除种子失败: {torrent.name} (ID: {torrent.id})")
            except Exception as e:
                logger.error(f"删除种子时出错: {torrent.name} (ID: {torrent.id}), 错误: {str(e)}")
                
        if deleted_count == 0:
            logger.info("没有发现需要删除的错误种子")
        else:
            logger.info(f"删除完成，共删除 {deleted_count} 个错误种子")
            
        # Update last run time in config
        self.__update_config()

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "password": self._password,
            "delete_files": self._delete_files,
            "error_patterns": "\n".join(self._error_patterns),
            "last_run_time": self._last_run_time
        })

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        """
        Stop scheduled tasks
        """
        try:
            if self._scheduler.get_job("Transmission错误种子清理"):
                self._scheduler.remove_job("Transmission错误种子清理")
        except Exception as e:
            logger.error(f"停止定时任务出错: {str(e)}")

    @eventmanager.register(EventType.PluginAction)
    def handle_manual_clean(self, event: Event):
        """
        Handle manual clean event
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "transmission_error_clean":
                return
            logger.info("收到手动清理错误种子命令，开始执行...")
            self._task()

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
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
                                    'md': 3
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
                                    'md': 3
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
                                    'md': 3
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
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '检查周期',
                                            'placeholder': '12h'
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
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'error_patterns',
                                            'label': '错误信息匹配模式（每行一个）',
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
                                            'text': '本插件会定期检查Transmission中的种子状态，删除匹配指定错误信息的种子\n'
                                                    '错误信息匹配模式支持多行，每行一个匹配关键词（不区分大小写）\n'
                                                    '检查周期格式：数字+单位（如12h、30m、1d）\n'
                                                    '若未勾选"删除文件"，则只删除种子任务不删除文件',
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
                                            'text': '警告：文件删除操作不可逆，请谨慎操作！',
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
            "cron": "12h",
            "onlyonce": False,
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
        Get plugin page
        """
        # You can add a simple status page if needed
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
        pass

    def get_command(self) -> List[Dict[str, Any]]:
        pass
