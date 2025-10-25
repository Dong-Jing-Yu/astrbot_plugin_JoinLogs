
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig
from astrbot.api import logger


@register("astrbot_plugin_JoinLogs", "东经雨", "记录入群时的一些信息", "1.1")
class JoinLogsPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
    
    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.GROUP_INCREASE)
    async def event_monitoring(self, event: AiocqhttpMessageEvent):
        """监听进群/退群事件"""
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return


        client = event.bot
        group_id: int = raw.get("group_id", 0)
        user_id: int = raw.get("user_id", 0)
        # 进群申请事件
        if (
            self.conf["enable_audit"]
            and raw.get("post_type") == "request"
            and raw.get("request_type") == "group"
            and raw.get("sub_type") == "add"
        ):
            comment = raw.get("comment")
            flag = raw.get("flag", "")
            nickname = (await client.get_stranger_info(user_id=user_id))[
                "nickname"
            ] or "未知昵称"
            reply = f"[进群申请]\n昵称：{nickname}\nQQ：{user_id}\nflag：{flag}"
            if comment:
                reply += f"\n{comment}"
            logger.info(f"测试:{reply}")
            # await event.send(event.plain_result(reply))


    @filter.command("查",alias={'查入群','查进群'})
    async def Cha(self, event: AstrMessageEvent):
        pass
        
    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
