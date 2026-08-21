#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notification.py - 消息机器人通知模块
功能：发送钉钉、企业微信机器人消息，用于基站/挂载点上下线提醒
"""

import json
import time
import queue
import urllib.request
import urllib.error
import hmac
import hashlib
import base64
from threading import Thread, Lock
from urllib.parse import quote_plus

from . import config
from .logger import log_info, log_error, log_warning


def _send_request(url, payload, headers=None, timeout=10):
    """发送HTTP POST请求"""
    data = json.dumps(payload).encode('utf-8')
    req_headers = {'Content-Type': 'application/json'}
    if headers:
        req_headers.update(headers)
    
    try:
        req = urllib.request.Request(url, data=data, headers=req_headers, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        log_error(f"通知请求失败: HTTP {e.code}, {e.read().decode('utf-8')}")
        return e.code, None
    except Exception as e:
        log_error(f"通知请求异常: {e}")
        return None, str(e)


def _dingtalk_sign(secret):
    """生成钉钉签名"""
    timestamp = str(int(round(time.time() * 1000)))
    secret_enc = secret.encode('utf-8')
    string_to_sign = f"{timestamp}\n{secret}"
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def send_dingtalk(webhook_url, title, message, secret=None):
    """发送钉钉群机器人消息
    
    Args:
        webhook_url: 钉钉机器人Webhook地址
        title: 消息标题
        message: 消息内容
        secret: 加签密钥(可选，钉钉机器人安全设置中设置的密钥)
    """
    url = webhook_url
    # 如果提供了加签密钥，自动附加签名参数到URL
    if secret and secret.strip():
        timestamp, sign = _dingtalk_sign(secret.strip())
        separator = '&' if '?' in url else '?'
        url = f"{url}{separator}timestamp={timestamp}&sign={sign}"
    
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": message
        }
    }
    
    status, resp = _send_request(url, payload)
    if status == 200 and resp:
        try:
            result = json.loads(resp)
            if result.get('errcode') == 0:
                log_info(f"钉钉通知发送成功: {title}")
                return True
            else:
                log_error(f"钉钉通知发送失败: {result.get('errmsg')}")
                return False
        except Exception:
            log_error(f"钉钉通知响应解析失败: {resp}")
            return False
    else:
        log_error(f"钉钉通知HTTP请求失败: status={status}")
        return False


def send_wecom(webhook_url, title, message):
    """发送企业微信机器人消息
    
    Args:
        webhook_url: 企业微信机器人Webhook地址
        title: 消息标题
        message: 内容
    """
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": f"**{title}**\n\n{message}"
        }
    }
    
    status, resp = _send_request(webhook_url, payload)
    if status == 200 and resp:
        try:
            result = json.loads(resp)
            if result.get('errcode') == 0:
                log_info(f"企业微信通知发送成功: {title}")
                return True
            else:
                log_error(f"企业微信通知发送失败: {result.get('errmsg')}")
                return False
        except Exception:
            log_error(f"企业微信通知响应解析失败: {resp}")
            return False
    else:
        log_error(f"企业微信通知HTTP请求失败: status={status}")
        return False


def _build_message(event_type, event_data):
    """构建通知消息"""
    event_titles = {
        'base_station_online': '✅ 挂载点上线',
        'base_station_offline': '❌ 挂载点下线',
        'mount_online': '✅ 移动站上线',
        'mount_offline': '❌ 移动站下线',
    }
    
    title = event_titles.get(event_type, '系统通知')
    
    mount_name = event_data.get('mount_name', '未知')
    ip_address = event_data.get('ip_address', '未知')
    connect_time = event_data.get('connect_time', '未知')
    reason = event_data.get('reason', '')
    uptime = event_data.get('uptime', '')
    
    if event_type.startswith('base_station'):
        # 挂载点(基站/上传端)上线/下线
        owner = event_data.get('owner', '未知')
        lines = [
            f"**挂载点：** {mount_name}",
            f"**所属用户：** {owner}",
        ]
        if event_type == 'base_station_online':
            lines.append(f"**上线时间：** {connect_time}")
        elif event_type == 'base_station_offline':
            lines.append(f"**下线时间：** {connect_time}")
            if uptime:
                lines.append(f"**连接时长：** {uptime} 秒")
            if reason:
                lines.append(f"**下线原因：** {reason}")
    else:
        # 移动站(用户连接/下载端)上线/下线
        username = event_data.get('username', '未知')
        lines = [
            f"**用户：** {username}",
            f"**挂载点：** {mount_name}",
            f"**IP 地址：** {ip_address}",
        ]
        if event_type == 'mount_online':
            lines.append(f"**接入时间：** {connect_time}")
        elif event_type == 'mount_offline':
            lines.append(f"**下线时间：** {connect_time}")
            if uptime:
                lines.append(f"**连接时长：** {uptime} 秒")
            if reason:
                lines.append(f"**下线原因：** {reason}")
    
    return title, "\n".join(lines)

def send_notification(bot, event_type, event_data):
    """向单个机器人发送通知
    
    Args:
        bot: 机器人配置字典
        event_type: 事件类型
        event_data: 事件数据
    """
    if not bot.get('enabled'):
        return False
    
    events = bot.get('events', [])
    if event_type not in events:
        return False
    
    platform = bot.get('platform')
    webhook_url = bot.get('webhook_url')
    if not webhook_url:
        log_warning(f"机器人 {bot.get('name')} 缺少 Webhook 地址")
        return False
    
    title, message = _build_message(event_type, event_data)
    
    if platform == 'dingtalk':
        secret = bot.get('secret')
        return send_dingtalk(webhook_url, title, message, secret)
    elif platform == 'wecom':
        return send_wecom(webhook_url, title, message)
    else:
        log_warning(f"不支持的机器人平台: {platform}")
        return False


def notify_base_station_online(mount_name, ip_address, owner=None, connect_time=None):
    """基站(挂载点上传端)上线通知"""
    notify('base_station_online', {
        'mount_name': mount_name,
        'ip_address': ip_address,
        'owner': owner or '未知',
        'connect_time': connect_time or time.strftime('%Y-%m-%d %H:%M:%S')
    })

def notify_base_station_offline(mount_name, ip_address, owner=None, connect_time=None, uptime=0, reason=""):
    """基站(挂载点上传端)下线通知"""
    notify('base_station_offline', {
        'mount_name': mount_name,
        'ip_address': ip_address,
        'owner': owner or '未知',
        'connect_time': connect_time or time.strftime('%Y-%m-%d %H:%M:%S'),
        'uptime': uptime,
        'reason': reason
    })

_notification_bots_loader = None

# 机器人配置缓存（DB查询移出热路径，带TTL定期刷新）
_bot_cache = None
_bot_cache_time = 0.0
_BOT_CACHE_TTL = 30  # 秒
_bot_cache_lock = Lock()

# 通知发送：使用单个工作线程处理队列，避免每个事件新建线程
_send_queue = queue.Queue(maxsize=1000)
_worker_started = False
_worker_lock = Lock()


def _get_notification_bots():
    """获取启用的机器人配置（带TTL缓存，避免在NTRIP连接热路径上频繁查询DB）"""
    global _bot_cache, _bot_cache_time, _notification_bots_loader
    now = time.time()
    with _bot_cache_lock:
        if _bot_cache is None or (now - _bot_cache_time) > _BOT_CACHE_TTL:
            if _notification_bots_loader is None:
                # 延迟导入，避免启动时循环依赖
                from .database import get_notification_bots
                _notification_bots_loader = get_notification_bots
            try:
                _bot_cache = _notification_bots_loader()
            except Exception as e:
                log_error(f"加载机器人配置缓存失败: {e}")
                _bot_cache = []
            _bot_cache_time = now
        return _bot_cache


def invalidate_notification_bots_cache():
    """清除机器人配置缓存（添加/修改/删除机器人配置后调用）"""
    global _bot_cache, _bot_cache_time
    with _bot_cache_lock:
        _bot_cache = None
        _bot_cache_time = 0.0


def _send_worker():
    """通知发送工作线程：串行消费队列"""
    while True:
        item = _send_queue.get()
        if item is None:
            break
        bots, event_type, event_data = item
        for bot in bots:
            try:
                send_notification(bot, event_type, event_data)
            except Exception as e:
                log_error(f"发送通知给机器人 {bot.get('name')} 失败: {e}")


def notify(event_type, event_data):
    """触发事件通知，使用缓存+队列异步发送给所有订阅的机器人

    Args:
        event_type: 事件类型，如 'mount_online', 'mount_offline'
        event_data: 事件数据字典
    """
    try:
        bots = _get_notification_bots()
        if not bots:
            return

        global _worker_started
        with _worker_lock:
            if not _worker_started:
                _worker_started = True
                Thread(target=_send_worker, daemon=True).start()

        try:
            _send_queue.put_nowait((bots, event_type, event_data))
        except queue.Full:
            # 通知队列已满时直接丢弃，避免阻塞NTRIP连接热路径
            pass
    except Exception as e:
        log_error(f"触发通知失败: {e}")


def notify_mount_online(mount_name, ip_address, username=None, connect_time=None):
    """挂载点(用户连接/下载端)上线通知"""
    notify('mount_online', {
        'mount_name': mount_name,
        'username': username or '未知',
        'ip_address': ip_address,
        'connect_time': connect_time or time.strftime('%Y-%m-%d %H:%M:%S')
    })


def notify_mount_offline(mount_name, ip_address, username=None, connect_time=None, uptime=0, reason=""):
    """挂载点(用户连接/下载端)下线通知"""
    notify('mount_offline', {
        'mount_name': mount_name,
        'username': username or '未知',
        'ip_address': ip_address,
        'connect_time': connect_time or time.strftime('%Y-%m-%d %H:%M:%S'),
        'uptime': uptime,
        'reason': reason
    })
