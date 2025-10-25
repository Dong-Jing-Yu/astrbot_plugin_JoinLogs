
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
        # 推荐使用框架提供的 data 目录（防止插件升级/重装覆盖数据）
        try:
            data_dir = StarTools.get_data_dir()  # 返回 Path 对象（若框架支持）
        except Exception:
            # 兜底：当前工作目录下 data（不推荐长期使用）
            data_dir = Path("./data")
        self.data_dir: Path = Path(data_dir) / "astrbot_plugin_joinlogs"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "joinlogs.db"

        # sqlite 连接；使用 check_same_thread False + lock 以在 async handler 中同步访问
        self._db_lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_db()
        logger.info(f"[JoinLogs] DB 初始化: {self.db_path}")

    def _init_db(self):
        with self._db_lock:
            cur = self._conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS join_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qq INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                status TEXT NOT NULL,       -- 'prepared' | 'joined'
                flag TEXT,                  -- 请求 flag（处理申请时常见）
                comment TEXT,               -- 用户填写的入群备注/问题
                answers TEXT,               -- 如果你收集了问答，可用 JSON 存储
                raw TEXT,                   -- 原始事件 JSON（便于调试）
                ts INTEGER                  -- 时间戳（秒）
            );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_qq ON join_logs(qq);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_group ON join_logs(group_id);")
            self._conn.commit()

    def _insert_prepared(self, qq, group_id, flag=None, comment=None, raw=None):
        ts = int(time.time())
        with self._db_lock:
            cur = self._conn.cursor()
            cur.execute("""
                INSERT INTO join_logs (qq, group_id, status, flag, comment, answers, raw, ts)
                VALUES (?, ?, 'prepared', ?, ?, ?, ?, ?)
            """, (qq, group_id, flag, comment, json.dumps(None), json.dumps(raw), ts))
            self._conn.commit()

    def _finalize_join(self, qq, group_id, raw=None):
        # 如果有已存在的 prepared，更新为 joined；否则插入一条 joined 记录
        ts = int(time.time())
        with self._db_lock:
            cur = self._conn.cursor()
            cur.execute("""
                UPDATE join_logs
                SET status='joined', raw=?, ts=?
                WHERE qq=? AND group_id=? AND status='prepared'
            """, (json.dumps(raw), ts, qq, group_id))
            if cur.rowcount == 0:
                # 没有 prepared 的记录 -> 插入一条 joined
                cur.execute("""
                    INSERT INTO join_logs (qq, group_id, status, flag, comment, answers, raw, ts)
                    VALUES (?, ?, 'joined', ?, ?, ?, ?, ?)
                """, (qq, group_id, None, None, json.dumps(None), json.dumps(raw), ts))
            self._conn.commit()

    def _delete_records(self, qq, group_id=None):
        # 删除某个群的某个 qq 记录；如果 group_id 为 None，则删除该 qq 的所有记录
        with self._db_lock:
            cur = self._conn.cursor()
            if group_id is None:
                cur.execute("DELETE FROM join_logs WHERE qq=?", (qq,))
            else:
                cur.execute("DELETE FROM join_logs WHERE qq=? AND group_id=?", (qq, group_id))
            self._conn.commit()

    def _query_by_qq(self, qq):
        with self._db_lock:
            cur = self._conn.cursor()
            cur.execute("SELECT id, qq, group_id, status, flag, comment, answers, raw, ts FROM join_logs WHERE qq=? ORDER BY ts DESC", (qq,))
            rows = cur.fetchall()
        results = []
        for r in rows:
            results.append({
                "id": r[0],
                "qq": r[1],
                "group_id": r[2],
                "status": r[3],
                "flag": r[4],
                "comment": r[5],
                "answers": json.loads(r[6]) if r[6] else None,
                "raw": json.loads(r[7]) if r[7] else None,
                "ts": r[8]
            })
        return results

    # —— 事件监听：接收所有事件，然后基于 raw_message 做判断（兼容 OneBot 常见结构）
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_all(self, event: AstrMessageEvent):
        try:
            raw = getattr(event.message_obj, "raw_message", None) or {}
            # Safety: raw 可能是对象也可能是 dict
            if not isinstance(raw, dict):
                try:
                    raw = dict(raw)
                except Exception:
                    # 保留原样作为字符串
                    raw = {"_raw_str": str(raw)}

            # ---- 1) 申请阶段（OneBot: post_type == 'request' & request_type == 'group'）
            post_type = raw.get("post_type")
            if post_type == "request" and raw.get("request_type") == "group":
                qq = int(raw.get("user_id") or raw.get("user_id", 0))
                group_id = int(raw.get("group_id") or raw.get("group_id", 0))
                flag = raw.get("flag")
                comment = raw.get("comment", "")
                # 写入 prepared（如果你希望避免重复写入，可先检查是否已存在）
                self._insert_prepared(qq, group_id, flag=flag, comment=comment, raw=raw)
                logger.info(f"[JoinLogs] prepared: qq={qq} group={group_id} flag={flag} comment={comment}")
                # 不主动回复，除非你想发送一条提示
                return

            # ---- 2) 入群成功通知（OneBot: post_type=='notice' && notice_type=='group_increase'）
            if post_type == "notice" and raw.get("notice_type") == "group_increase":
                # OneBot notice for join often contains 'user_id' (the joined user)
                qq = int(raw.get("user_id") or 0)
                group_id = int(raw.get("group_id") or 0)
                if qq and group_id:
                    self._finalize_join(qq, group_id, raw=raw)
                    logger.info(f"[JoinLogs] finalized join: qq={qq} group={group_id}")
                return

            # ---- 3) 退群/被踢（OneBot: notice_type == 'group_decrease'）
            if post_type == "notice" and raw.get("notice_type") == "group_decrease":
                qq = int(raw.get("user_id") or 0)
                group_id = int(raw.get("group_id") or 0)
                # sub_type 可能是 'leave' 或 'kick'
                if qq:
                    # 删除该 qq 在该群的记录
                    self._delete_records(qq, group_id=group_id)
                    logger.info(f"[JoinLogs] deleted records for qq={qq} group={group_id}")
                return

        except Exception as e:
            logger.error(f"[JoinLogs] 事件处理出错: {e}")

    # —— 指令：按 qq 查询（/joinlog <qq>）
    @filter.command("joinlog", alias={'查入群', '查进群'})
    async def cmd_joinlog(self, event: AstrMessageEvent, qq: int = None):
        try:
            # 1) 确定查询 QQ
            if qq is None:
                qq = int(event.get_sender_id())
    
            # 2) 确定当前群 ID（优先 raw_message，再兜底 event）
            raw = getattr(event.message_obj, "raw_message", None)
            group_id = None
            try:
                if isinstance(raw, dict):
                    group_id = int(raw.get("group_id") or 0)
                else:
                    if hasattr(raw, "get"):
                        g = raw.get("group_id")
                        if g:
                            group_id = int(g)
                    if group_id is None and hasattr(raw, "group_id"):
                        group_id = int(getattr(raw, "group_id"))
            except Exception:
                group_id = None
    
            if not group_id:
                try:
                    if hasattr(event, "get_group_id"):
                        group_id = int(event.get_group_id())
                    elif hasattr(event, "group_id"):
                        group_id = int(event.group_id)
                except Exception:
                    group_id = None
    
            if not group_id:
                yield event.plain_result("无法确定当前群号，请在群内使用此指令。")
                return
    
            # 3) 拉出该 QQ 的所有记录并过滤出 当前群 的
            rows = self._query_by_qq(qq)
            rows = [r for r in rows if int(r.get("group_id") or 0) == int(group_id)]
    
            if not rows:
                yield event.plain_result(f"无 {qq} 在本群（{group_id}）的记录。")
                return
    
            # 4) 解析 comment（"问题:xxx\\n答案:xxx"），构造纯人类可读输出
            out_lines = []
            for r in rows:
                t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("ts") or 0))
                comment = r.get("comment") or ""
                q_text = "-"
                a_text = "-"
    
                if comment:
                    try:
                        lines = [ln.strip() for ln in comment.splitlines() if ln.strip() != ""]
                        # 优先寻找带标签的行
                        for ln in lines:
                            if ln.startswith("问题："):
                                q_text = ln[len("问题:"):].strip() or "-"
                            elif ln.startswith("答案："):
                                a_text = ln[len("答案:"):].strip() or "-"
                        # 若未找到标签，则降级：第一行为问题，其余为答案
                        if (q_text == "-" or a_text == "-") and lines:
                            if q_text == "-":
                                q_text = lines[0]
                            if a_text == "-" and len(lines) > 1:
                                a_text = "\n".join(lines[1:]) or "-"
                    except Exception:
                        q_text = comment or "-"
                        a_text = "-"
    
                out_lines.append(f"QQ: {qq}\n时间: {t}\n问题: {q_text}\n答案: {a_text}")
    
            # 5) 返回（多条记录以空行分隔）
            human_text = "\n\n".join(out_lines)
            yield event.plain_result(human_text)
    
        except Exception as e:
            logger.exception(e)
            yield event.plain_result("查询失败，详情见日志。")

        
    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        try:
            self._conn.close()
        except:
            pass
