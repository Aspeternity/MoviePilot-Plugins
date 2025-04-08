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

class OrphanFilesCleaner(PluginBase):
    """
    Transmission 冗余文件清理插件
    功能：定期扫描下载目录，删除未关联种子的文件和空目录
    """

    # ==============================
    #          插件元数据
    # ==============================
    plugin_name = "冗余文件清理"                   # 插件显示名称
    plugin_desc = "清理Transmission中未做种的文件"  # 功能描述
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"        # Material Design 图标
    plugin_version = "1.2"                     # 语义化版本号
    plugin_author = "Asp"                  # 开发者名称
    author_url = "https://github.com/Aspeternity"   # 开发者链接
    plugin_config_prefix = "orphancleaner_"      # 配置前缀
    plugin_order = 35                            # 加载顺序
    auth_level = 1                               # 权限等级（1=管理员）

    def __init__(self):
        """
        插件初始化
        """
        super().__init__()
        # 任务调度器实例
        self._scheduler: Optional[BackgroundScheduler] = None
        # 当前插件配置
        self._current_config = {
            "enabled": False,            # 启用状态
            "cron": "0 0 * * *",          # 定时任务表达式
            "host": "localhost",          # Transmission主机地址
            "port": 9091,                 # Transmission端口
            "username": "",               # 用户名
            "password": "",               # 密码
            "download_dir": "",           # 下载目录绝对路径
            "delete_empty_dir": True,     # 是否删除空目录
            "dry_run": True,              # 模拟运行模式
            "onlyonce": False             # 立即运行一次
        }

    def init_plugin(self, config: dict = None):
        """
        插件初始化入口
        :param config: 插件配置字典
        """
        # 合并传入配置
        if config:
            self._current_config.update(config)
            logger.debug(f"接收到新配置: {config}")

        # 停止现有服务
        self.stop_service()

        # 如果插件已启用
        if self._current_config.get("enabled"):
            logger.info("插件初始化：启用状态")

            # 立即运行一次
            if self._current_config.get("onlyonce"):
                logger.info("立即执行一次扫描任务")
                self._scan_and_clean()
                # 重置立即运行标志
                self._current_config["onlyonce"] = False
                self.update_config(self._current_config)

            # 配置定时任务
            cron = self._current_config.get("cron")
            if cron:
                try:
                    # 初始化调度器
                    self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                    # 解析cron表达式
                    cron_params = self._parse_cron(cron)
                    # 添加定时任务
                    self._scheduler.add_job(
                        func=self._scan_and_clean,
                        trigger='cron',
                        **cron_params
                    )
                    self._scheduler.start()
                    logger.info(f"定时任务已启动，执行周期: {cron}")
                except Exception as e:
                    logger.error(f"定时任务配置失败: {str(e)}")
                    self.post_message(
                        mtype=NotificationType.Manual,
                        title="插件初始化错误",
                        text=f"定时任务配置错误: {str(e)}"
                    )
        else:
            logger.info("插件初始化：禁用状态")

    def _parse_cron(self, cron_str: str) -> dict:
        """
        解析标准cron表达式为APScheduler参数
        :param cron_str: 分 时 日 月 周 格式的字符串
        :return: 参数字典
        :raises ValueError: 表达式无效时抛出
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
            logger.debug(f"发现 {len(torrents)} 个活跃种子")
            for torrent in torrents:
                download_dir = Path(torrent.download_dir).resolve()
                for file in torrent.files():
                    file_path = (download_dir / file.name).resolve()
                    active_files.add(str(file_path))
            logger.debug(f"收集到 {len(active_files)} 个活跃文件路径")
            return active_files
        except Exception as e:
            logger.error(f"获取活跃文件失败: {str(e)}")
            self.post_message(
                mtype=NotificationType.Manual,
                title="连接错误",
                text=f"无法连接Transmission: {str(e)}"
            )
            return set()

    def _scan_files(self) -> set:
        """
        扫描下载目录获取所有文件/目录路径
        :return: 路径集合
        """
        all_files = set()
        try:
            download_path = Path(self._current_config["download_dir"]).resolve()
            logger.debug(f"开始扫描目录: {download_path}")
            # 递归遍历所有条目
            for entry in download_path.rglob('*'):
                all_files.add(str(entry.resolve()))
            logger.debug(f"扫描到 {len(all_files)} 个文件/目录")
            return all_files
        except Exception as e:
            logger.error(f"目录扫描失败: {str(e)}")
            self.post_message(
                mtype=NotificationType.Manual,
                title="目录错误",
                text=f"无法扫描目录: {str(e)}"
            )
            return set()

    def _scan_and_clean(self):
        """
        执行扫描和清理操作
        """
        logger.info("========== 开始冗余文件扫描 ==========")
        try:
            # 验证必要配置
            if not self._current_config["download_dir"]:
                raise ValueError("未配置下载目录")

            # 获取文件集合
            active_files = self._get_active_files()
            all_files = self._scan_files()
            orphan_files = all_files - active_files
            logger.info(f"发现 {len(orphan_files)} 个待处理项")

            # 分类处理
            deleted = []
            files_to_delete = [Path(f) for f in orphan_files if Path(f).is_file()]
            dirs_to_delete = [Path(f) for f in orphan_files if Path(f).is_dir()]

            # 处理文件
            for path in files_to_delete:
                try:
                    logger.debug(f"尝试删除文件: {path}")
                    if not self._current_config["dry_run"]:
                        path.unlink(missing_ok=True)
                        deleted.append(str(path))
                except Exception as e:
                    logger.error(f"文件删除失败 {path}: {str(e)}")

            # 处理目录（按深度倒序）
            dirs_to_delete.sort(key=lambda x: len(x.parts), reverse=True)
            for path in dirs_to_delete:
                if self._current_config["delete_empty_dir"] and not any(path.iterdir()):
                    try:
                        logger.debug(f"尝试删除目录: {path}")
                        if not self._current_config["dry_run"]:
                            path.rmdir()
                            deleted.append(str(path))
                    except Exception as e:
                        logger.error(f"目录删除失败 {path}: {str(e)}")

            # 发送通知
            self.post_message(
                mtype=NotificationType.SiteMessage,
                title="【清理完成】",
                text=f"扫描目录: {self._current_config['download_dir']}\n"
                     f"发现冗余项: {len(orphan_files)}\n"
                     f"实际删除: {len(deleted)}\n"
                     f"模拟模式: {'✅' if self._current_config['dry_run'] else '❌'}"
            )
            logger.info(f"清理完成，共删除 {len(deleted)} 项")
        except Exception as e:
            logger.error(f"扫描过程出错: {str(e)}")
            self.post_message(
                mtype=NotificationType.Manual,
                title="【清理异常】",
                text=f"错误信息: {str(e)}"
            )
        logger.info("========== 扫描任务结束 ==========\n")

    def stop_service(self):
        """
        停止插件服务
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
                logger.info("定时任务已停止")
        except Exception as e:
            logger.error(f"停止调度器失败: {str(e)}")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        生成 Vuetify 配置表单
        :return: (表单组件列表, 默认值字典)
        """
        return [
            {
                "component": "VForm",
                "content": [
                    # 第一行：核心开关组
                    {
                        "component": "VRow",
                        "content": [
                            # 启用开关
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
                                            "persistent-hint": True,
                                            "color": "primary"
                                        }
                                    }
                                ]
                            },
                            # 模拟模式
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
                                            "persistent-hint": True,
                                            "color": "warning"
                                        }
                                    }
                                ]
                            },
                            # 删除空目录
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
                    # 第二行：目录与定时配置
                    {
                        "component": "VRow",
                        "content": [
                            # 下载目录输入
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "v-model": "download_dir",
                                            "label": "下载目录",
                                            "placeholder": "/data/downloads",
                                            "rules": [{
                                                "required": True,
                                                "message": "必须填写下载目录",
                                                "trigger": "blur"
                                            }],
                                            "prepend-icon": "mdi-folder",
                                            "clearable": True
                                        }
                                    }
                                ]
                            },
                            # 定时任务配置
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "v-model": "cron",
                                            "label": "执行周期",
                                            "placeholder": "0 0 * * *",
                                            "rules": [{
                                                "required": True,
                                                "message": "必须填写cron表达式",
                                                "trigger": "blur"
                                            }],
                                            "prepend-icon": "mdi-clock",
                                            "hint": "标准cron表达式（分 时 日 月 周）"
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
                            # 主机地址
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "v-model": "host",
                                            "label": "主机地址",
                                            "placeholder": "localhost",
                                            "prepend-icon": "mdi-server"
                                        }
                                    }
                                ]
                            },
                            # 端口号
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
                                            "placeholder": "9091",
                                            "prepend-icon": "mdi-ethernet"
                                        }
                                    }
                                ]
                            },
                            # 用户名
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
                            # 密码输入
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
                                            "prepend-icon": "mdi-lock",
                                            "clearable": True
                                        }
                                    }
                                ]
                            },
                            # 提示信息
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "border": "left",
                                            "colored-border": True,
                                            "dense": True,
                                            "icon": "mdi-information",
                                            "text": "操作提示：\n"
                                                    "1. 首次使用请开启模拟模式\n"
                                                    "2. 确保下载目录与Transmission配置一致\n"
                                                    "3. 修改配置后需重启插件"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], self._current_config

    def update_config(self, config: dict):
        """
        更新配置到数据库
        :param config: 新配置字典
        """
        self._current_config.update(config)
        super().update_config(self._current_config)
        logger.debug("配置已更新")

    def get_state(self) -> bool:
        """
        获取插件启用状态
        :return: 是否启用
        """
        return self._current_config.get("enabled", False)

    def get_page(self) -> List[dict]:
        """
        无独立页面返回空列表
        """
        return []
