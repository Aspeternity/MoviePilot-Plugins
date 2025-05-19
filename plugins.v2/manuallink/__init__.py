import datetime
import re
import threading
import traceback
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import schemas
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.types import EventType
from app.utils.system import SystemUtils

lock = threading.Lock()


class ManualLink(_PluginBase):
    # 插件名称
    plugin_name = "手动硬链接工具"
    # 插件描述
    plugin_desc = "手动选择文件或目录进行硬链接操作。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    # 插件版本
    plugin_version = "1.0"
    # 插件作者
    plugin_author = "Aspeternity"
    # 作者主页
    author_url = "https://github.com/Aspeternity"
    # 插件配置项ID前缀
    plugin_config_prefix = "manuallink_"
    # 加载顺序
    plugin_order = 4
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _scheduler = None
    _enabled = False
    _notify = False
    _onlyonce = False
    _size = 0
    # 排除关键词
    _exclude_keywords = ""
    # 退出事件
    _event = threading.Event()

    def init_plugin(self, config: dict = None):
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._notify = config.get("notify")
            self._onlyonce = config.get("onlyonce")
            self._exclude_keywords = config.get("exclude_keywords") or ""
            self._size = config.get("size") or 0

        # 停止现有任务
        self.stop_service()

        if self._onlyonce:
            # 关闭一次性开关
            self._onlyonce = False
            # 保存配置
            self.__update_config()

    def __update_config(self):
        """
        更新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "notify": self._notify,
            "onlyonce": self._onlyonce,
            "exclude_keywords": self._exclude_keywords,
            "size": self._size
        })

    @eventmanager.register(EventType.PluginAction)
    def remote_link(self, event: Event):
        """
        远程手动硬链接
        """
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "manual_link":
                return
            
            # 获取参数
            source_path = event_data.get("source_path")
            target_path = event_data.get("target_path")
            is_dir = event_data.get("is_dir", False)
            
            if not source_path or not target_path:
                self.post_message(channel=event.event_data.get("channel"),
                                title="参数错误",
                                text="需要提供源路径和目标路径",
                                userid=event.event_data.get("user"))
                return
                
            self.post_message(channel=event.event_data.get("channel"),
                            title="开始手动硬链接 ...",
                            userid=event.event_data.get("user"))
            
            # 执行硬链接
            if is_dir:
                success, failed = self.link_directory(Path(source_path), Path(target_path))
            else:
                success, failed = self.link_file(Path(source_path), Path(target_path))
            
            # 发送结果通知
            self.post_message(channel=event.event_data.get("channel"),
                            title="手动硬链接完成！",
                            text=f"成功: {success}, 失败: {failed}",
                            userid=event.event_data.get("user"))

    def link_file(self, source_path: Path, target_path: Path) -> Tuple[int, int]:
        """
        硬链接单个文件
        :param source_path: 源文件路径
        :param target_path: 目标路径(可以是目录或完整文件路径)
        :return: (成功数, 失败数)
        """
        if not source_path.exists():
            return (0, 1)
            
        if target_path.is_dir():
            # 如果目标是目录，则在目标目录下创建同名文件
            target_file = target_path / source_path.name
        else:
            target_file = target_path
            
        # 检查排除关键词
        if self._check_exclude(source_path):
            return (0, 1)
            
        # 执行硬链接
        if self._size and float(self._size) > 0 and source_path.stat().st_size < float(self._size) * 1024:
            logger.info(f"{source_path} 文件大小小于最小文件大小，复制...")
            code, errmsg = SystemUtils.copy(source_path, target_file)
        else:
            code, errmsg = SystemUtils.link(source_path, target_file)
            
        if code == 0:
            logger.info(f"{source_path} 硬链接成功 -> {target_file}")
            if self._notify:
                self.post_message(
                    mtype=NotificationType.Manual,
                    title=f"{source_path.name} 硬链接完成！",
                    text=f"目标路径：{target_file}"
                )
            return (1, 0)
        else:
            logger.warn(f"{source_path} 硬链接失败：{errmsg}")
            if self._notify:
                self.post_message(
                    mtype=NotificationType.Manual,
                    title=f"{source_path.name} 硬链接失败！",
                    text=f"原因：{errmsg or '未知'}"
                )
            return (0, 1)

    def link_directory(self, source_dir: Path, target_dir: Path) -> Tuple[int, int]:
        """
        硬链接整个目录
        :param source_dir: 源目录路径
        :param target_dir: 目标目录路径
        :return: (成功数, 失败数)
        """
        if not source_dir.exists() or not source_dir.is_dir():
            return (0, 1)
            
        success = 0
        failed = 0
        
        # 遍历目录下所有文件
        for file_path in SystemUtils.list_files(source_dir, ['.*']):
            # 计算相对路径
            rel_path = file_path.relative_to(source_dir)
            target_path = target_dir / rel_path
            
            # 创建目标目录结构
            if not target_path.parent.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                
            # 硬链接文件
            s, f = self.link_file(file_path, target_path)
            success += s
            failed += f
            
        return (success, failed)

    def _check_exclude(self, file_path: Path) -> bool:
        """
        检查文件是否应该被排除
        """
        if not self._exclude_keywords:
            return False
            
        file_path_str = str(file_path)
        for keyword in self._exclude_keywords.split("\n"):
            if keyword and re.findall(keyword, file_path_str):
                logger.info(f"{file_path} 命中排除关键词 {keyword}，跳过处理")
                return True
        return False

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        定义远程控制命令
        :return: 命令关键字、事件、描述、附带数据
        """
        return [{
            "cmd": "/manual_link",
            "event": EventType.PluginAction,
            "desc": "手动硬链接文件或目录",
            "category": "管理",
            "data": {
                "action": "manual_link"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        return [{
            "path": "/manual_link",
            "endpoint": self.manual_link_api,
            "methods": ["POST"],
            "summary": "手动硬链接API",
            "description": "通过API手动硬链接文件或目录",
        }]

    def manual_link_api(self, source_path: str, target_path: str, is_dir: bool = False) -> schemas.Response:
        """
        API调用手动硬链接
        """
        try:
            if is_dir:
                success, failed = self.link_directory(Path(source_path), Path(target_path))
            else:
                success, failed = self.link_file(Path(source_path), Path(target_path))
                
            return schemas.Response(
                success=True,
                message=f"操作完成，成功: {success}, 失败: {failed}",
                data={
                    "success": success,
                    "failed": failed
                }
            )
        except Exception as e:
            return schemas.Response(
                success=False,
                message=str(e)
            )

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
                                            'model': 'notify',
                                            'label': '发送通知',
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
                                            'model': 'size',
                                            'label': '最小文件大小（KB）',
                                            'placeholder': '小于此大小的文件将直接复制'
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
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'exclude_keywords',
                                            'label': '排除关键词',
                                            'rows': 2,
                                            'placeholder': '每一行一个关键词'
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
                                            'text': '使用说明：\n'
                                                   '1. 通过远程命令或API调用手动硬链接功能\n'
                                                   '2. 最小文件大小：小于此值的文件将直接复制而非硬链接\n'
                                                   '3. 排除关键词：匹配这些关键词的文件将被跳过'
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
            "notify": False,
            "onlyonce": False,
            "exclude_keywords": "",
            "size": ""
        }

    def get_page(self) -> List[dict]:
        # 可以在这里添加一个简单的操作页面
        return [
            {
                'component': 'VCard',
                'content': [
                    {
                        'component': 'VCardText',
                        'props': {
                            'class': 'pa-0'
                        },
                        'content': [
                            {
                                'component': 'VAlert',
                                'props': {
                                    'type': 'info',
                                    'variant': 'tonal',
                                    'text': '请通过远程命令或API调用手动硬链接功能'
                                }
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self):
        """
        退出插件
        """
        if self._scheduler:
            self._scheduler.remove_all_jobs()
            if self._scheduler.running:
                self._event.set()
                self._scheduler.shutdown()
                self._event.clear()
            self._scheduler = None
