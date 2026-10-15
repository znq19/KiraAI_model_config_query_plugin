import re
from core.plugin import BasePlugin, logger, register_tool as tool, on, Priority
from core.chat.message_utils import KiraMessageBatchEvent, KiraMessageEvent
from core.chat import MessageChain
from core.chat.message_elements import Text


class ModelConfigQueryPlugin(BasePlugin):
    def __init__(self, ctx, cfg):
        super().__init__(ctx, cfg)
        self.enabled = cfg.get("enabled", True)
        self.enable_command = cfg.get("enable_command", True)
        self.allowed_users = [str(uid) for uid in cfg.get("allowed_users", [])]
        self.command_word = cfg.get("command_word", "/模型")
        self.enable_tool = cfg.get("enable_tool", True)
        self.permission_denied_message = cfg.get("permission_denied_message", "抱歉，您没有权限查询模型配置。")
        # 是否显示提供商名称（默认 true，如果设为 false 则只显示模型 ID）
        self.show_provider_name = cfg.get("show_provider_name", True)

    async def initialize(self):
        if self.enabled:
            logger.info(f"模型配置查询插件已启动，白名单: {self.allowed_users}, 命令词: {self.command_word}, 命令响应: {self.enable_command}, 工具: {self.enable_tool}, 显示提供商名称: {self.show_provider_name}")

    async def terminate(self):
        logger.info("模型配置查询插件已停止")

    def _is_allowed(self, user_id: str) -> bool:
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    def _get_provider_name_map(self) -> dict:
        """获取 provider_id -> provider_name 的映射"""
        providers_config = self.ctx.config.get_config("providers", {})
        mapping = {}
        for pid, config in providers_config.items():
            name = config.get("name", pid)
            mapping[pid] = name
        return mapping

    def _format_model_value(self, value: str) -> str:
        """格式化模型配置值，显示提供商名称 + 人类可读模型名。

        3.0 起引用值冒号后是内部稳定 ID（uuid 形态），可读名在 ModelInfo.model_name；
        2.x 冒号后本就是可读名。经 provider_mgr.get_model_info 解析（两代签名一致），
        解析失败原样回退，绝不影响命令可用性。"""
        if not value or ":" not in value:
            return value
        provider_id, model_id = value.split(":", 1)
        display = model_id
        try:
            pm = getattr(self.ctx, "provider_mgr", None)
            if pm is not None:
                info = pm.get_model_info(provider_id, model_id)
                if info is not None:
                    display = getattr(info, "model_name", None) or info.model_id
        except Exception:
            pass
        if self.show_provider_name:
            provider_map = self._get_provider_name_map()
            provider_name = provider_map.get(provider_id, provider_id)
            return f"{provider_name}:{display}"
        else:
            # 只显示模型名，完全隐藏提供商
            return display

    def _get_model_config(self) -> str:
        models_config = self.ctx.config.get_config("models", {})
        model_keys = [
            "default_llm",
            "default_fast_llm",
            "default_vlm",
            "default_tts",
            "default_stt",
            "default_embedding",
            "default_rerank",
            "default_image",
            "default_video"
        ]
        lines = []
        for key in model_keys:
            value = models_config.get(key)
            if value:
                formatted = self._format_model_value(value)
                lines.append(f"{key}: {formatted}")
        if not lines:
            return "未配置任何默认模型。"
        return "\n".join(lines)

    @tool(
        name="query_model_config",
        description="查询当前 KiraAI 配置的默认模型名称（如默认 LLM、VLM、TTS、STT、嵌入模型等）。",
        params={"type": "object", "properties": {}, "required": []}
    )
    async def tool_query_model_config(self, event: KiraMessageBatchEvent) -> str:
        user_id = str(event.messages[-1].sender.user_id) if event.messages else None
        if not user_id or not self._is_allowed(user_id):
            return self.permission_denied_message
        return f"当前 KiraAI 默认模型配置如下：\n{self._get_model_config()}"

    @on.im_message(priority=Priority.HIGH)
    async def on_im_message(self, event: KiraMessageEvent):
        if not self.enabled or not self.enable_command:
            return

        text = "".join(elem.text for elem in event.message.chain if isinstance(elem, Text))
        if not text:
            return

        if text.strip() == self.command_word or text.strip().startswith(self.command_word + " "):
            user_id = None
            if hasattr(event.message, 'sender') and hasattr(event.message.sender, 'user_id'):
                user_id = str(event.message.sender.user_id)
            else:
                logger.warning("无法获取用户ID")
                return

            sid = self._get_sid(event)

            if not self._is_allowed(user_id):
                reply = self.permission_denied_message
            else:
                reply = f"当前 KiraAI 默认模型配置如下：\n{self._get_model_config()}"

            await self.ctx.send_message_chain(
                session=sid,
                chain=MessageChain([Text(reply)])
            )
            event.discard(force=True)
            event.stop()

    def _get_sid(self, event: KiraMessageEvent) -> str:
        if hasattr(event, 'sid'):
            return event.sid
        if hasattr(event, 'session') and hasattr(event.session, 'sid'):
            return event.session.sid
        return "default"