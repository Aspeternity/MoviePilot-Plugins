from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path
import os
import json
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.config import settings
from app.log import logger
from app.modules.transmission import Transmission
from transmission_rpc import Torrent
from app.plugins.plugin import PluginBase
from app.schemas.types import EventType, NotificationType
from app.db import db
from app.db.models import Plugin

class OrphanFilesCleaner(PluginBase):
    """
    Transmission 冗余文件清理插件 (MoviePilot V2 优化版)
    功能：自动扫描并删除未关联种子的文件和空目录
    """

    # ==================== 插件元数据 ====================
    plugin_id = "orphan_cleaner"  # 必须：唯一英文标识（数据库存储用）
    plugin_name = "冗余文件清理"    # 必须：插件显示名称
    plugin_desc = "自动清理Transmission中未做种的文件和空目录"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    plugin_version = "2.3.0"
    plugin_author = "Asp"
    author_url = "https://github.com/Aspeternity"
    plugin_config_prefix = f"{plugin_id}_"  # 自动生成配置前缀
    plugin_order = 35
    auth_level = 1

    def __init__(self):
        """初始化插件实例"""
        super().__init__()
        # 初始化调度器
        self._scheduler: Optional[BackgroundScheduler] = None
        # 默认配置（必须与表单字段完全匹配）
        self._default_config = {
            "enabled": False,
            "cron": "0 0 * * *",
            "host": "localhost",
            "port": 9091,
            "username": "",
            "password": "",
            "download_dir": "",
            "delete_empty_dir": True,
            "dry_run": True,
            "onlyonce": False
        }
        # 当前配置（初始化时从数据库加载）
        self._current_config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """
        从数据库加载插件配置
        :return: 配置字典
        """
        with db.connection():
            # 查询数据库记录
            plugin = Plugin.get_or_none(Plugin.plugin_id == self.plugin_id)
            if plugin:
                config = json.loads(plugin.config) if plugin.config else {}
                logger.debug(f"从数据库加载配置: {config}")
                return {**self._default_config, **config}
            else:
                # 首次运行时创建记录
                Plugin.create(
                    plugin_id=self.plugin_id,
                    plugin_name=self.plugin_name,
                    config=json.dumps(self._default_config)
                )
                logger.info("初始化插件数据库记录")
                return self._default_config

    def init_plugin(self, config: dict = None):
        """
        插件初始化入口
        :param config: 前端传入的配置字典
        """
        # 合并配置
        if config:
            self._current_config.update(config)
            logger.debug(f"接收到新配置: {config}")

        # 保存配置到数据库
        self._save_config()

        # 停止现有服务
        self.stop_service()

        # 如果插件启用
        if self._current_config["enabled"]:
            # 立即运行一次
            if self._current_config["onlyonce"]:
                logger.info("立即执行一次扫描任务")
                self._scan_and_clean()
                # 重置立即运行标志
                self._current_config["onlyonce"] = False
                self._save_config()

            # 配置定时任务
            if self._current_config["cron"]:
                try:
                    self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                    self._scheduler.add_job(
                        func=self._scan_and_clean,
                        trigger='cron',
                        **self._parse_cron(self._current_config["cron"])
                    )
                    self._scheduler.start()
                    logger.info(f"定时任务启动成功，执行周期: {self._current_config['cron']}")
                except Exception as e:
                    logger.error(f"定时任务配置失败: {str(e)}")
                    self.post_message(
                        mtype=NotificationType.Manual,
                        title="插件初始化错误",
                        text=f"定时任务配置错误: {str(e)}"
                    )

    def _save_config(self):
        """保存当前配置到数据库"""
        with db.connection():
            plugin = Plugin.get_or_none(Plugin.plugin_id == self.plugin_id)
            if plugin:
                plugin.config = json.dumps(self._current_config)
                plugin.save()
                logger.debug("配置已保存到数据库")
            else:
                logger.error("找不到插件数据库记录！")

    def _parse_cron(self, cron_str: str) -> Dict[str, str]:
        """解析cron表达式"""
        fields = cron_str.strip().split()
        if len(fields) != 5:
            raise ValueError("无效的cron表达式，需要5个字段：分 时 日 月 周")
        return {
            "minute": fields[0],
            "hour": fields[1],
            "day": fields[2],
            "month": fields[3],
            "day_of_week": fields[4]
        }

    # ==================== 核心功能 ====================
    def _get_active_files(self) -> set:
        """获取所有活跃种子的文件路径"""
        active_files = set()
        try:
            client = Transmission(
                host=self._current_config["host"],
                port=self._current_config["port"],
                username=self._current_config["username"],
                password=self._current_config["password"]
            )
            torrents, error = client.get_torrents()
            if error:
                raise Exception(error)
            
            for torrent in torrents:
                download_dir = Path(torrent.download_dir).resolve()
                for file in torrent.files():
                    file_path = (download_dir / file.name).resolve()
                    active_files.add(str(file_path))
            return active_files
        except Exception as e:
            logger.error(f"获取活跃文件失败: {str(e)}")
            return set()

    def _scan_and_clean(self):
        """执行扫描清理操作"""
        logger.info("开始冗余文件扫描...")
        try:
            # 验证必要配置
            if not self._current_config["download_dir"]:
                raise ValueError("未配置下载目录")

            # 获取文件集合
            active_files = self._get_active_files()
            all_files = set(str(p.resolve()) 
                          for p in Path(self._current_config["download_dir"]).rglob('*'))
            orphan_files = all_files - active_files

            # 分类处理
            deleted = []
            for path_str in orphan_files:
                path = Path(path_str)
                if not path.exists():
                    continue

                try:
                    if path.is_file():
                        if not self._current_config["dry_run"]:
                            path.unlink()
                        deleted.append(f"文件: {path}")
                    elif path.is_dir() and self._current_config["delete_empty_dir"]:
                        if not any(path.iterdir()):
                            if not self._current_config["dry_run"]:
                                path.rmdir()
                            deleted.append(f"空目录: {path}")
                except Exception as e:
                    logger.error(f"删除失败 {path}: {str(e)}")

            # 发送通知
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="【冗余清理完成】",
                text=f"扫描目录: {self._current_config['download_dir']}\n"
                     f"发现冗余项: {len(orphan_files)}\n"
                     f"实际删除: {len(deleted)}\n"
                     f"模拟模式: {'是' if self._current_config['dry_run'] else '否'}"
            )
            logger.info(f"清理完成，共删除 {len(deleted)} 项")
        except Exception as e:
            logger.error(f"扫描过程出错: {str(e)}")
            self.post_message(
                mtype=NotificationType.Manual,
                title="【清理异常】",
                text=f"错误信息: {str(e)}"
            )

    # ==================== Vuetify 表单 ====================
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """生成 Vuetify 配置表单"""
        return [
            {
                "component": "VForm",
                "content": [
                    # 第一行：开关组
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "v-model": "enabled",
                                            "label": "启用插件",
                                            "hint": "主控制开关",
                                            "persistent-hint": True
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "v-model": "dry_run",
                                            "label": "模拟模式",
                                            "hint": "测试运行不实际删除",
                                            "persistent-hint": True
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "v-model": "delete_empty_dir",
                                            "label": "删除空目录",
                                            "hint": "自动清理空文件夹",
                                            "persistent-hint": True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 第二行：目录与定时
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "v-model": "download_dir",
                                            "label": "下载目录",
                                            "rules": [{"required": True, "message": "必填项"}],
                                            "prepend-icon": "mdi-folder"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "v-model": "cron",
                                            "label": "执行周期",
                                            "rules": [{"required": True, "message": "必填项"}],
                                            "prepend-icon": "mdi-clock"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 第三行：连接配置
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "v-model": "host",
                                            "label": "主机地址",
                                            "prepend-icon": "mdi-server"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "v-model": "port",
                                            "label": "端口号",
                                            "type": "number",
                                            "prepend-icon": "mdi-network-port"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "v-model": "username",
                                            "label": "用户名",
                                            "prepend-icon": "mdi-account"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 第四行：密码与提示
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "v-model": "password",
                                            "label": "密码",
                                            "type": "password",
                                            "prepend-icon": "mdi-lock"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "border": "left",
                                            "text": "操作提示：\n1. 首次使用请开启模拟模式\n2. 确保下载目录路径正确",
                                            "style": "white-space: pre-line;"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], self._current_config

    # ==================== 必要接口 ====================
    def stop_service(self):
        """停止插件服务"""
        if self._scheduler:
            self._scheduler.remove_all_jobs()
            if self._scheduler.running:
                self._scheduler.shutdown()
            self._scheduler = None

    def get_state(self) -> bool:
        """获取插件启用状态"""
        return self._current_config["enabled"]

    def get_page(self) -> List[dict]:
        """无独立页面返回空列表"""
        return []
