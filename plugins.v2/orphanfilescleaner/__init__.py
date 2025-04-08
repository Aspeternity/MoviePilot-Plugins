from typing import List, Tuple, Dict, Any, Union
from app.log import logger
from app.modules.transmission import Transmission
from transmission_rpc.torrent import Torrent
from app.plugins import _PluginBase
import os
import glob

class TransmissionCleaner(_PluginBase):
    # Plugin metadata
    plugin_name = "Transmission冗余文件清理"
    plugin_desc = "查找并删除Transmission下载目录中未关联任何种子的冗余文件"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    plugin_version = "2.3.1"
    plugin_author = "Asp"
    author_url = "https://github.com/Aspeternity"
    plugin_config_prefix = "transmissioncleaner_"
    plugin_order = 31
    auth_level = 1

    # Plugin configuration
    _onlyonce: bool = False
    _transmission: Transmission = None
    _host: str = None
    _port: int = None
    _username: str = None
    _password: str = None
    _download_dir: str = None
    _dry_run: bool = True  # Default to dry run for safety

    def init_plugin(self, config: dict = None):
        if config:
            self._onlyonce = config.get("onlyonce")
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            self._download_dir = config.get("download_dir")
            self._dry_run = config.get("dry_run", True)
            
        if self._onlyonce:
            try:
                self._transmission = Transmission(self._host, self._port, self._username, self._password)
                self._task()
                self._onlyonce = False
                self.__update_config()
            except Exception as e:
                logger.error(f"初始化Transmission连接失败: {str(e)}")

    def _task(self):
        if not self._transmission:
            logger.error("Transmission客户端未初始化")
            return
            
        if not self._download_dir:
            logger.error("未配置下载目录")
            return
            
        # Get all active torrents from Transmission
        torrents, error = self._transmission.get_torrents()
        if error:
            logger.error(f"获取种子列表失败: {error}")
            return
            
        # Get all files associated with active torrents
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
                
        # Walk through download directory to find redundant files
        redundant_files = []
        total_size = 0
        
        # Check for files directly in download directory
        for root, dirs, files in os.walk(self._download_dir):
            for file in files:
                file_path = os.path.normpath(os.path.join(root, file))
                if file_path not in active_files:
                    redundant_files.append(file_path)
                    total_size += os.path.getsize(file_path)
                    
        if not redundant_files:
            logger.info("没有找到冗余文件")
            return
            
        # Log found redundant files
        logger.info(f"找到 {len(redundant_files)} 个冗余文件，总大小: {self._format_size(total_size)}")
        for file in redundant_files:
            logger.info(f"冗余文件: {file}")
            
        # Delete files if not in dry run mode
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

    def _format_size(self, size_bytes):
        """Convert bytes to human-readable format"""
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
            "dry_run": self._dry_run
        })

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

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
            "download_dir": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def get_state(self) -> bool:
        return self._onlyonce

    def stop_service(self):
        pass
