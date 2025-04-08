from typing import List, Tuple, Dict, Any, Union
from app.log import logger
from app.modules.transmission import Transmission
from transmission_rpc.torrent import Torrent
from app.plugins import _PluginBase
import os

class TransmissionCleaner(_PluginBase):
    # 插件名称
    plugin_name = "Transmission冗余文件清理"
    # 插件描述
    plugin_desc = "查找并删除Transmission下载目录中未关联任何种子的冗余文件"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/cleaner.png"
    # 插件版本
    plugin_version = "1.2"
    # 插件作者
    plugin_author = "Asp"
    # 作者主页
    author_url = "https://github.com/Aspeternity"
    # 插件配置项ID前缀
    plugin_config_prefix = "transmissioncleaner_"
    # 加载顺序
    plugin_order = 31
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _onlyonce: bool = False
    _transmission: Transmission = None
    _host: str = None
    _port: int = None
    _username: str = None
    _password: str = None
    _download_dir: str = None
    _dry_run: bool = True
    _connection_status: str = "未测试"

    def init_plugin(self, config: dict = None):
        if config:
            self._onlyonce = config.get("onlyonce")
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            self._download_dir = config.get("download_dir")
            self._dry_run = config.get("dry_run", True)
            self._connection_status = config.get("connection_status", "未测试")
            
        if self._onlyonce:
            try:
                self._transmission = Transmission(self._host, self._port, self._username, self._password)
                self._task()
                self._onlyonce = False
                self.__update_config()
            except Exception as e:
                logger.error(f"初始化Transmission连接失败: {str(e)}")
                self._connection_status = f"连接失败: {str(e)}"

    def _task(self):
        if not self._transmission:
            logger.error("Transmission客户端未初始化")
            return
            
        if not self._download_dir:
            logger.error("未配置下载目录")
            return
            
        # 获取所有活跃种子
        torrents, error = self._transmission.get_torrents()
        if error:
            logger.error(f"获取种子列表失败: {error}")
            return
            
        # 获取所有活跃种子关联的文件
        active_files = set()
        for torrent in torrents:
            try:
                files = self._transmission.get_files(torrent.id)
                for file in files:
                    file_path = os.path.join(torrent.download_dir, file.name)
                    active_files.add(os.path.normpath(file_path))
            except Exception as e:
                logger.warning(f"获取种子文件失败: {torrent.name}, 错误: {str(e)}")
                continue
                
        # 查找冗余文件
        redundant_files = []
        total_size = 0
        
        for root, dirs, files in os.walk(self._download_dir):
            for file in files:
                file_path = os.path.normpath(os.path.join(root, file))
                if file_path not in active_files:
                    redundant_files.append(file_path)
                    total_size += os.path.getsize(file_path)
                    
        if not redundant_files:
            logger.info("没有找到冗余文件")
            return
            
        logger.info(f"找到 {len(redundant_files)} 个冗余文件，总大小: {self._format_size(total_size)}")
        
        if not self._dry_run:
            deleted_count = 0
            deleted_size = 0
            for file in redundant_files:
                try:
                    file_size = os.path.getsize(file)
                    os.remove(file)
                    deleted_count += 1
                    deleted_size += file_size
                    logger.info(f"已删除: {file}")
                except Exception as e:
                    logger.error(f"删除文件失败 {file}: {str(e)}")
                    
            logger.info(f"删除完成，共删除 {deleted_count} 个文件，释放空间: {self._format_size(deleted_size)}")
        else:
            logger.info("当前处于模拟模式，不会实际删除文件")

    def _test_connection(self):
        """测试Transmission连接"""
        try:
            self._transmission = Transmission(self._host, self._port, self._username, self._password)
            stats = self._transmission.get_session_stats()
            if stats:
                self._connection_status = "连接成功"
                logger.info("Transmission连接测试成功")
                return True
        except Exception as e:
            self._connection_status = f"连接失败: {str(e)}"
            logger.error(f"Transmission连接测试失败: {str(e)}")
            return False
        return False

    def _format_size(self, size_bytes):
        """格式化文件大小"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"

    def __update_config(self):
        self.update_config({
            "onlyonce": self._onlyonce,
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "password": self._password,
            "download_dir": self._download_dir,
            "dry_run": self._dry_run,
            "connection_status": self._connection_status
        })

    def get_state(self) -> bool:
        return self._onlyonce

    def stop_service(self):
        pass

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        定义插件命令
        """
        return [{
            "cmd": "/transmission_clean",
            "event": "cmd",
            "desc": "Transmission冗余文件清理",
            "category": "下载器",
            "data": {
                "action": "transmission_clean"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        定义插件API
        """
        return [{
            "path": "/test_connection",
            "endpoint": self.test_connection,
            "methods": ["GET"],
            "summary": "测试Transmission连接",
            "description": "测试Transmission连接状态"
        }]

    def test_connection(self):
        """
        API接口：测试连接
        """
        success = self._test_connection()
        return {
            "success": success,
            "message": self._connection_status
        }

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        定义插件配置表单
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
                                    'md': 6
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
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'dry_run',
                                            'label': '模拟运行（不实际删除）',
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'download_dir',
                                            'label': '下载目录',
                                            'placeholder': '/data/downloads'
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
                                            'text': '连接状态: ' + self._connection_status,
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
                                        'component': 'VBtn',
                                        'props': {
                                            'block': True,
                                            'variant': 'tonal',
                                            'prepend-icon': 'mdi-connection',
                                            'text': '测试Transmission连接',
                                            'color': 'primary',
                                            '@click': 'transmissioncleaner_test_connection'
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
                                            'text': '本插件会扫描Transmission下载目录，查找不属于任何活跃种子的文件\n'
                                                    '建议首次使用时启用"模拟运行"模式，确认无误后再关闭模拟模式进行实际删除',
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
            "onlyonce": False,
            "dry_run": True,
            "host": "192.168.1.100",
            "port": 9091,
            "username": "admin",
            "password": "password",
            "download_dir": "",
            "connection_status": "未测试"
        }

    def get_page(self) -> List[dict]:
        pass
