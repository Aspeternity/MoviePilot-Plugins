from typing import List, Tuple, Dict, Any, Union, Optional
from datetime import datetime
from app.log import logger
from app.modules.transmission import Transmission
from transmission_rpc.torrent import Torrent
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.core.event import eventmanager, Event
from apscheduler.triggers.cron import CronTrigger
import time
import re

class TransmissionTrackerCleaner(_PluginBase):
    # Plugin metadata
    plugin_name = "Transmission失效种子清理"
    plugin_desc = "定时清理Transmission中Tracker失效的种子及文件"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    plugin_version = "1.4"
    plugin_author = "Aspeternity"
    author_url = "https://github.com/Aspeternity"
    plugin_config_prefix = "transmissiontrackercleaner_"
    plugin_order = 32
    auth_level = 1

    # Plugin configuration
    _enabled: bool = False
    _cron: str = "0 12 * * *"  # 默认每天中午12点运行
    _onlyonce: bool = False
    _transmission: Transmission = None
    _host: str = None
    _port: int = None
    _username: str = None
    _password: str = None
    _delete_files: bool = True
    _dry_run: bool = True  # 新增模拟运行选项
    _error_patterns: List[str] = ["Torrent not exists", "not registered", "未注册"]
    _last_run_time: str = None

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled", False)
            self._cron = config.get("cron", "0 12 * * *")
            self._onlyonce = config.get("onlyonce", False)
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            self._delete_files = config.get("delete_files", True)
            self._dry_run = config.get("dry_run", True)  # 新增模拟运行选项
            error_patterns_str = config.get("error_patterns", "")
            self._error_patterns = [p.strip() for p in error_patterns_str.split('\n') if p.strip()]
            
        # 停止现有任务
        self.stop_service()
        
        # 启动新任务
        if self._enabled or self._onlyonce:
            self._transmission = Transmission(self._host, self._port, self._username, self._password)
            
            # 立即运行一次
            if self._onlyonce:
                logger.info("Transmission错误种子清理服务启动，立即运行一次")
                self._scheduler.add_job(self._task, 'date',
                                       run_date=datetime.now() + timedelta(seconds=3),
                                       name="Transmission错误种子清理")
                # 关闭一次性标志
                self._onlyonce = False
                self.__update_config()
                
            # 启动周期任务
            if self._enabled:
                try:
                    # 解析cron表达式
                    if self._cron:
                        try:
                            cron_trigger = CronTrigger.from_crontab(self._cron)
                            self._scheduler.add_job(self._task, cron_trigger,
                                                  name="Transmission错误种子清理")
                            logger.info(f"Transmission错误种子清理服务启动，周期：{self._cron}")
                        except Exception as e:
                            logger.error(f"cron表达式解析失败：{self._cron}")
                            self._enabled = False
                            self.__update_config()
                except Exception as e:
                    logger.error(f"定时任务配置错误：{str(e)}")
                    self._enabled = False
                    self.__update_config()

    def _task(self):
        if not self._transmission:
            logger.error("Transmission客户端未初始化")
            return
            
        logger.info("开始检查Transmission中的错误种子...")
        self._last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 获取所有种子
        torrents, error = self._transmission.get_torrents()
        if error:
            logger.error(f"获取种子列表失败: {error}")
            return
            
        # 检查每个种子的错误状态
        deleted_count = 0
        for torrent in torrents:
            if not torrent.error or torrent.error == 0:
                continue
                
            # 检查错误信息是否匹配模式
            error_str = getattr(torrent, "errorString", "") or ""
            if not any(pattern.lower() in error_str.lower() for pattern in self._error_patterns):
                continue
                
            # 记录要删除的种子
            logger.info(f"发现错误种子: {torrent.name} (ID: {torrent.id}), 错误信息: {error_str}")
            
            # 如果是模拟运行模式，只记录不删除
            if self._dry_run:
                logger.info(f"模拟模式: 将删除种子: {torrent.name} (ID: {torrent.id})")
                deleted_count += 1
                continue
                
            # 实际删除种子
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
            if self._dry_run:
                logger.info(f"模拟运行完成，共发现 {deleted_count} 个需要删除的错误种子")
            else:
                logger.info(f"删除完成，共删除 {deleted_count} 个错误种子")
            
        # 更新配置中的最后运行时间
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
            "dry_run": self._dry_run,  # 新增模拟运行选项
            "error_patterns": "\n".join(self._error_patterns),
            "last_run_time": self._last_run_time
        })

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        """
        停止定时任务
        """
        try:
            if self._scheduler.get_job("Transmission错误种子清理"):
                self._scheduler.remove_job("Transmission错误种子清理")
        except Exception as e:
            logger.error(f"停止定时任务出错: {str(e)}")

    @eventmanager.register(EventType.PluginAction)
    def handle_manual_clean(self, event: Event):
        """
        处理手动清理事件
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期(cron表达式)',
                                            'placeholder': '0 12 * * *'
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
                                            'rows': 2,
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
                                                    '错误信息匹配模式支持多行，每行一个匹配关键词(不区分大小写)\n'
                                                    '执行周期使用标准cron表达式(分 时 日 月 周)\n'
                                                    '若未勾选"删除文件"，则只删除种子任务不删除文件\n'
                                                    '启用"模拟运行"模式时只记录不实际删除',
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
                                            'text': '警告：文件删除操作不可逆，请谨慎操作！\n'
                                                    '建议首次使用时启用"模拟运行"模式',
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
            "cron": "0 12 * * *",
            "onlyonce": False,
            "delete_files": True,
            "dry_run": True,
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
        pass

    def get_command(self) -> List[Dict[str, Any]]:
        pass
