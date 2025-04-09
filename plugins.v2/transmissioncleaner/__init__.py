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
    plugin_version = "1.7"  # 版本号更新
    plugin_author = "Aspeternity"
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
    _download_dirs: List[str] = None  # 修改为列表存储多个目录
    _dry_run: bool = True  # Default to dry run for safety
    _delete_images_nfo: bool = False  # 是否删除图片/NFO文件
    _delete_system_files: bool = False  # 是否删除系统文件

    def init_plugin(self, config: dict = None):
        if config:
            self._onlyonce = config.get("onlyonce")
            self._host = config.get("host")
            self._port = config.get("port")
            self._username = config.get("username")
            self._password = config.get("password")
            # 处理多目录输入，按行分割并去除空行和前后空格
            dirs_str = config.get("download_dirs", "")
            self._download_dirs = [d.strip() for d in dirs_str.split('\n') if d.strip()]
            self._dry_run = config.get("dry_run", True)
            self._delete_images_nfo = config.get("delete_images_nfo", False)
            self._delete_system_files = config.get("delete_system_files", False)
            
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
            
        if not self._download_dirs:
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
                
        # Walk through download directories to find redundant files
        redundant_files = []
        total_size = 0
        
        for download_dir in self._download_dirs:
            if not os.path.isdir(download_dir):
                logger.warning(f"目录不存在或不可访问: {download_dir}")
                continue
                
            # Check for files directly in download directory
            for root, dirs, files in os.walk(download_dir):
                # 如果不删除系统文件，则跳过@eaDir目录
                if not self._delete_system_files and '@eaDir' in root:
                    continue
                    
                for file in files:
                    file_path = os.path.normpath(os.path.join(root, file))
                    
                    # 如果不删除系统文件，则跳过系统文件
                    if not self._delete_system_files and (
                        file.startswith('SYNOINDEX_') or 
                        file.startswith('.') or 
                        file == 'Thumbs.db'
                    ):
                        continue
                        
                    # 根据用户选择过滤图片/NFO文件
                    if not self._delete_images_nfo:
                        file_ext = os.path.splitext(file)[1].lower()
                        if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.nfo', '.txt']:
                            logger.debug(f"跳过图片/NFO文件: {file_path}")
                            continue
                            
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
            "download_dirs": "\n".join(self._download_dirs),  # 保存为多行文本
            "dry_run": self._dry_run,
            "delete_images_nfo": self._delete_images_nfo,
            "delete_system_files": self._delete_system_files
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
                                            'model': 'dry_run',
                                            'label': '模拟运行',
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
                                            'model': 'delete_images_nfo',
                                            'label': '删除图片/NFO',
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
                                            'model': 'delete_system_files',
                                            'label': '删除系统文件',
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
                                            'model': 'download_dirs',
                                            'label': '下载目录（每行一个）',
                                            'placeholder': '/data/downloads\n/data/downloads2',
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
                                            'text': '本插件会扫描Transmission下载目录，查找不属于任何活跃种子的文件\n'
                                                    '建议首次使用时启用"模拟运行"模式，确认无误后再关闭模拟模式进行实际删除\n'
                                                    '若未勾选"删除图片/NFO文件"，则跳过.jpg/.png/.nfo等文件\n'
                                                    '若未勾选"删除系统文件"，则跳过@eaDir/SYNOINDEX_*等系统文件\n'
                                                    '可以在"下载目录"中输入多个目录，每行一个',
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
            "delete_images_nfo": False,
            "delete_system_files": False,
            "host": "192.168.1.100",
            "port": 9091,
            "username": "admin",
            "password": "password",
            "download_dirs": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def get_state(self) -> bool:
        return self._onlyonce

    def stop_service(self):
        pass
