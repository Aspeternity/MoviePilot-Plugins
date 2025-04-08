from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path
import os
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.config import settings
from app.log import logger
from app.modules.transmission import Transmission
from transmission_rpc import Torrent
from app.plugins.plugin import PluginBase
from app.schemas.types import EventType, NotificationType
from app.utils.string import StringUtils

class OrphanFilesCleaner(PluginBase):
    # ==================================================
    #                插件元数据配置
    # ==================================================
    # 插件名称（显示在前端）
    plugin_name = "冗余文件清理"
    # 插件描述
    plugin_desc = "自动扫描Transmission下载目录，清理未关联种子的冗余文件和空目录。"
    # 插件图标（建议使用HTTPS链接）
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    # 插件版本（语义化版本格式）
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "Asp"
    # 作者主页
    author_url = "https://github.com/Aspeternity"
    # 插件配置前缀（用于数据库存储）
    plugin_config_prefix = "orphancleaner_"
    # 插件加载顺序（数字越小越先加载）
    plugin_order = 0
    # 权限级别（0-普通用户 1-管理员）
    auth_level = 1

    # ==================================================
    #                  初始化方法
    # ==================================================
    def __init__(self):
        super().__init__()
        # 调度器实例
        self._scheduler: Optional[BackgroundScheduler] = None
        # 当前配置
        self._current_config = {
            "enabled": False,         # 是否启用插件
            "cron": "0 0 * * *",      # 定时任务表达式
            "host": "localhost",      # Transmission主机地址
            "port": 9091,             # Transmission端口
            "username": "",           # 用户名
            "password": "",           # 密码
            "download_dir": "",       # 下载目录绝对路径
            "delete_empty_dir": True, # 是否删除空目录
            "dry_run": True,          # 模拟运行模式
            "onlyonce": False         # 立即运行一次
        }

    def init_plugin(self, config: dict = None):
        """
        插件初始化入口
        :param config: 插件配置字典
        """
        # 合并默认配置与传入配置
        if config:
            self._current_config.update(config)
            
        # 停止现有服务
        self.stop_service()

        # 如果插件启用
        if self._current_config.get("enabled"):
            # 立即运行一次
            if self._current_config.get("onlyonce"):
                self._scan_and_clean()
                # 重置立即运行标志
                self._current_config["onlyonce"] = False
                self.update_config(self._current_config)

            # 初始化定时任务
            cron = self._current_config.get("cron")
            if cron:
                try:
                    self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                    self._scheduler.add_job(
                        func=self._scan_and_clean,
                        trigger='cron',
                        **self._parse_cron(cron)
                    )
                    self._scheduler.start()
                    logger.info("定时任务启动成功")
                except Exception as e:
                    logger.error(f"定时任务配置错误: {str(e)}")

    def _parse_cron(self, cron_str: str) -> dict:
        """
        解析标准cron表达式为APScheduler参数
        :param cron_str: cron表达式（分 时 日 月 周）
        :return: 参数字典
        """
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

    # ==================================================
    #                  核心业务逻辑
    # ==================================================
    def _get_active_files(self) -> set:
        """
        获取所有活跃种子的文件路径集合
        :return: 文件绝对路径集合
        """
        active_files = set()
        try:
            # 初始化Transmission客户端
            client = Transmission(
                host=self._current_config["host"],
                port=self._current_config["port"],
                username=self._current_config["username"],
                password=self._current_config["password"]
            )
            # 获取种子列表
            torrents, error = client.get_torrents()
            if error:
                raise Exception(error)
            
            # 遍历种子收集文件路径
            for torrent in torrents:
                # 标准化下载目录路径
                download_dir = Path(torrent.download_dir).resolve()
                for file in torrent.files():
                    # 构建文件绝对路径
                    file_path = (download_dir / file.name).resolve()
                    active_files.add(str(file_path))
            return active_files
        except Exception as e:
            logger.error(f"获取活跃文件失败: {str(e)}")
            return set()

    def _scan_files(self) -> set:
        """
        扫描下载目录获取所有文件/目录路径
        :return: 路径集合
        """
        all_files = set()
        try:
            download_path = Path(self._current_config["download_dir"]).resolve()
            # 递归遍历所有条目
            for entry in download_path.rglob('*'):
                all_files.add(str(entry.resolve()))
            return all_files
        except Exception as e:
            logger.error(f"目录扫描失败: {str(e)}")
            return set()

    def _scan_and_clean(self):
        """
        执行扫描清理操作
        """
        logger.info("开始冗余文件扫描...")
        try:
            # 获取活跃文件集合
            active_files = self._get_active_files()
            # 扫描下载目录
            all_files = self._scan_files()
            # 计算差异
            orphan_files = all_files - active_files

            # 分类处理
            deleted = []
            files_to_delete = []
            dirs_to_delete = []
            
            # 分类文件类型
            for file_path in orphan_files:
                path = Path(file_path)
                if not path.exists():
                    continue
                if path.is_file():
                    files_to_delete.append(path)
                elif path.is_dir():
                    dirs_to_delete.append(path)

            # 删除文件
            for path in files_to_delete:
                logger.info(f"发现冗余文件: {path}")
                if not self._current_config["dry_run"]:
                    try:
                        path.unlink()
                        deleted.append(str(path))
                    except Exception as e:
                        logger.error(f"文件删除失败 {path}: {str(e)}")

            # 处理目录（按深度倒序）
            dirs_to_delete.sort(key=lambda x: len(x.parts), reverse=True)
            for path in dirs_to_delete:
                if self._current_config["delete_empty_dir"] and not any(path.iterdir()):
                    logger.info(f"发现空目录: {path}")
                    if not self._current_config["dry_run"]:
                        try:
                            path.rmdir()
                            deleted.append(str(path))
                        except Exception as e:
                            logger.error(f"目录删除失败 {path}: {str(e)}")

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
            logger.error(f"清理过程出错: {str(e)}")
            self.post_message(
                mtype=NotificationType.Manual,
                title="【冗余清理异常】",
                text=f"错误信息: {str(e)}"
            )

    # ==================================================
    #                  插件服务管理
    # ==================================================
    def stop_service(self):
        """停止插件服务"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止调度器失败: {str(e)}")

    # ==================================================
    #                  前端表单配置
    # ==================================================
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        构建插件配置表单（MoviePilot V2规范）
        :return: (表单组件列表, 表单默认值字典)
        """
        return [
            {
                "component": "VForm",
                "content": [
                    # 第一行：启用开关
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
                                            "model": "enabled",
                                            "label": "启用插件",
                                            "hint": "主开关控制插件运行状态"
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
                                            "model": "dry_run",
                                            "label": "模拟模式",
                                            "hint": "开启后仅记录不实际删除"
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
                                            "model": "delete_empty_dir",
                                            "label": "删除空目录",
                                            "hint": "自动清理无内容的目录"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 第二行：定时任务配置
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
                                            "model": "cron",
                                            "label": "执行周期",
                                            "placeholder": "0 0 * * *",
                                            "hint": "标准cron表达式（分 时 日 月 周）"
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
                                            "model": "download_dir",
                                            "label": "下载目录",
                                            "placeholder": "/data/downloads",
                                            "hint": "需与Transmission配置完全一致"
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
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "host",
                                            "label": "主机地址",
                                            "placeholder": "localhost"
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
                                            "model": "port",
                                            "label": "端口号",
                                            "type": "number",
                                            "placeholder": "9091"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 第四行：认证信息
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
                                            "model": "username",
                                            "label": "用户名",
                                            "placeholder": "admin"
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
                                            "model": "password",
                                            "label": "密码",
                                            "type": "password",
                                            "placeholder": "password"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 提示信息
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "⚠️ 操作提示 ⚠️\n"
                                                    "1. 首次使用请先开启模拟模式\n"
                                                    "2. 确保下载目录路径完全一致\n"
                                                    "3. 修改配置后需重启插件生效",
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

    # ==================================================
    #                  必要接口实现
    # ==================================================
    def get_state(self) -> bool:
        """返回插件启用状态"""
        return self._current_config.get("enabled", False)

    def get_page(self) -> List[dict]:
        """无独立页面返回空列表"""
        return []

    def update_config(self, config: dict):
        """更新配置到数据库"""
        self._current_config.update(config)
        super().update_config(self._current_config)

