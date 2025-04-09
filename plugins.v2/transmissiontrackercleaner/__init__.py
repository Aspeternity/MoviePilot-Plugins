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
    增强版：改进错误检测逻辑，增加调试信息
    """
    
    # 插件元数据
    plugin_name = "Transmission失效种子清理"
    plugin_desc = "定时清理Transmission中Tracker失效的种子及文件"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    plugin_version = "1.6"
    plugin_author = "Aspeternity"
    author_url = "https://github.com/Aspeternity"
    plugin_config_prefix = "transmissiontrackercleaner_"
    plugin_order = 32
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
    _error_patterns: List[str] = [
        "Torrent not exists",
        "not registered", 
        "未注册",
        "error",
        "fail",
        "unavailable"
    ]
    _last_run_time: str = None

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        if config:
            self._enabled = config.get("enabled", False)
            self._onlyonce = config.get("onlyonce", False)
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            self._delete_files = config.get("delete_files", True)
            self._dry_run = config.get("dry_run", True)
            
            error_patterns_str = config.get("error_patterns", "")
            self._error_patterns = [p.strip() for p in error_patterns_str.split('\n') if p.strip()]
            
        if self._onlyonce:
            try:
                logger.info("正在初始化Transmission连接...")
                self._transmission = Transmission(
                    self._host, 
                    self._port, 
                    self._username, 
                    self._password
                )
                logger.info("Transmission连接成功，开始执行清理任务...")
                self._task()
                self._onlyonce = False
                self.__update_config()
            except Exception as e:
                logger.error(f"Transmission连接初始化失败: {str(e)}")
                # 尝试重新连接
                try:
                    self._transmission = Transmission(
                        self._host,
                        self._port,
                        self._username,
                        self._password
                    )
                    logger.info("重新连接Transmission成功，重试任务...")
                    self._task()
                    self._onlyonce = False
                    self.__update_config()
                except Exception as e2:
                    logger.error(f"重试连接Transmission失败: {str(e2)}")

    def _task(self):
        """
        执行清理任务，增强错误检测逻辑
        """
        if not self._transmission:
            logger.error("Transmission客户端未初始化，无法执行任务")
            return
            
        logger.info("开始深度检查Transmission中的错误种子...")
        self._last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 获取所有种子（包括所有状态）
        try:
            torrents, error = self._transmission.get_torrents()
            if error:
                logger.error("获取种子列表时Transmission返回错误")
                return
        except Exception as e:
            logger.error(f"获取种子列表时发生异常: {str(e)}")
            return
            
        # 调试：记录种子总数和基本信息
        logger.debug(f"共获取到 {len(torrents)} 个种子")
        if not torrents:
            logger.info("Transmission中没有种子")
            return
            
        deleted_count = 0
        no_error_count = 0
        matched_error_count = 0
        error_code_dist = {}  # 错误码统计
        
        for torrent in torrents:
            # 获取种子详细信息
            error_code = getattr(torrent, "error", 0)
            error_str = getattr(torrent, "errorString", "") or ""
            status = getattr(torrent, "status", "unknown")
            
            # 统计错误码分布
            if error_code not in error_code_dist:
                error_code_dist[error_code] = 0
            error_code_dist[error_code] += 1
            
            # 调试日志
            logger.debug(
                f"种子检查: ID={torrent.id}, 名称='{torrent.name}'\n"
                f"状态={status}, 错误码={error_code}, 错误信息='{error_str}'\n"
                f"Tracker状态: {getattr(torrent, 'trackerStats', '无信息')}"
            )
            
            # 检查是否有错误（错误码不为0表示有错误）
            if error_code == 0:
                no_error_count += 1
                continue
                
            # 检查错误信息是否匹配任何模式（不区分大小写）
            is_match = False
            lower_error_str = error_str.lower()
            for pattern in self._error_patterns:
                if pattern.lower() in lower_error_str:
                    is_match = True
                    break
                    
            if not is_match:
                logger.debug(
                    f"种子 {torrent.name} 的错误信息不匹配任何模式\n"
                    f"错误信息: '{error_str}'\n"
                    f"当前模式: {self._error_patterns}"
                )
                continue
                
            matched_error_count += 1
            logger.info(
                f"发现匹配的错误种子: {torrent.name} (ID: {torrent.id})\n"
                f"错误码: {error_code}, 错误详情: '{error_str}'\n"
                f"Tracker状态: {getattr(torrent, 'trackerStats', '无信息')}"
            )
            
            # 模拟运行模式只记录不删除
            if self._dry_run:
                logger.info(f"[模拟模式] 将删除种子: {torrent.name} (ID: {torrent.id})")
                deleted_count += 1
                continue
                
            # 实际删除操作
            try:
                logger.info(f"正在删除种子: {torrent.name} (ID: {torrent.id})...")
                if self._transmission.delete_torrents(
                    delete_file=self._delete_files, 
                    ids=torrent.id
                ):
                    logger.info(f"成功删除种子: {torrent.name} (ID: {torrent.id})")
                    deleted_count += 1
                else:
                    logger.error(f"删除种子失败: {torrent.name} (ID: {torrent.id})")
            except Exception as e:
                logger.error(
                    f"删除种子时发生异常: {torrent.name} (ID: {torrent.id})\n"
                    f"错误: {str(e)}"
                )
                
        # 输出详细的统计信息
        logger.info("="*50)
        logger.info("任务执行结果统计:")
        logger.info(f"检查种子总数: {len(torrents)}")
        logger.info(f"无错误种子: {no_error_count}")
        logger.info(f"匹配的错误种子: {matched_error_count}")
        logger.info("错误码分布统计:")
        for code, count in error_code_dist.items():
            logger.info(f"错误码 {code}: {count} 个种子")
        
        if matched_error_count == 0:
            logger.warning("未发现匹配的错误种子，可能原因:")
            logger.warning("1. 错误信息不匹配当前配置的模式")
            logger.warning("2. Transmission返回的错误信息格式有变化")
            logger.warning("3. 种子可能处于其他错误状态")
            logger.warning(f"当前配置的错误模式: {self._error_patterns}")
            logger.warning("建议检查调试日志中的具体错误信息")
        else:
            if self._dry_run:
                logger.info(f"模拟运行完成，共发现 {deleted_count} 个需要删除的错误种子")
            else:
                logger.info(f"删除完成，共删除 {deleted_count} 个错误种子")
        
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
        pass

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
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'error_patterns',
                                            'label': '错误信息匹配模式(每行一个)',
                                            'placeholder': 'Torrent not exists\n未注册\nerror',
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
                                            'text': '使用说明:\n'
                                                    '1. 启用插件后可以立即运行一次\n'
                                                    '2. "模拟运行"模式下只记录不实际删除\n'
                                                    '3. 错误模式匹配不区分大小写\n'
                                                    '4. 常见错误模式示例:\n'
                                                    '   - Torrent not exists\n'
                                                    '   - not registered\n'
                                                    '   - 未注册\n'
                                                    '   - tracker error\n'
                                                    '   - unavailable',
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
                                                    '1. 文件删除操作不可逆！\n'
                                                    '2. 建议首次使用时启用"模拟运行"模式\n'
                                                    '3. 如果检测不到错误种子，请检查调试日志\n'
                                                    '4. 确保错误模式匹配实际的错误信息',
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
            "delete_files": True,
            "dry_run": True,
            "host": "192.168.1.100",
            "port": 9091,
            "username": "admin",
            "password": "password",
            "error_patterns": "Torrent not exists\n未注册\nerror",
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
        """
        获取API
        """
        pass

    def get_command(self) -> List[Dict[str, Any]]:
        """
        获取命令
        """
        pass
