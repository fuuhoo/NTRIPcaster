#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import json
import threading
from threading import Lock, RLock, Thread
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

from . import config
from . import logger
from .logger import log_system_event, log_error, log_warning, log_info, log_debug
from .rtcm2_manager import parser_manager as rtcm_manager  # 导入RTCM2解析管理器
from . import notification  # 导入通知模块
from . import database  # 导入数据库模块，用于记录上下线事件

@dataclass
class MountInfo:
    """挂载点信息数据类"""
    mount_name: str
    ip_address: str = ""
    user_agent: str = ""
    protocol_version: str = "1.0"
    connect_time: float = field(default_factory=time.time)
    connect_datetime: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    last_update: float = field(default_factory=time.time)
    
    # 添加socket引用，用于强制关闭连接
    client_socket: Optional[object] = None
    
    # 基站信息
    station_id: Optional[int] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    height: Optional[float] = None
    
    # 地理信息
    country: Optional[str] = None  # 国家代码（如CHN）
    city: Optional[str] = None     # 城市名称（如Beijing）
    
    # 数据统计
    total_bytes: int = 0
    total_messages: int = 0
    data_rate: float = 0.0
    data_count: int = 0
    last_data_time: Optional[float] = None
    
    # 状态信息
    status: str = 'online'  # 'online', 'offline'
    
    # STR表信息
    str_data: str = ""
    initial_str_generated: bool = False
    final_str_generated: bool = False
    
    custom_info: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def uptime(self) -> float:
        """运行时间（秒）"""
        return time.time() - self.connect_time
    
    @property
    def idle_time(self) -> float:
        """空闲时间（秒）"""
        if self.last_data_time:
            return time.time() - self.last_data_time
        return self.uptime
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式，用于JSON序列化"""
        
        return {
            'mount_name': self.mount_name,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'protocol_version': self.protocol_version,
            'connect_time': self.connect_time,
            'connect_datetime': self.connect_datetime,
            'last_update': self.last_update,
            'station_id': self.station_id,
            'lat': self.lat,
            'lon': self.lon,
            'height': self.height,
            'country': self.country,
            'city': self.city,
            'total_bytes': self.total_bytes,
            'total_messages': self.total_messages,
            'data_rate': self.data_rate,
            'data_count': self.data_count,
            'last_data_time': self.last_data_time,
            'status': self.status,
            'str_data': self.str_data,
            'initial_str_generated': self.initial_str_generated,
            'final_str_generated': self.final_str_generated,
            'custom_info': self.custom_info
        }
class ConnectionManager:
    """连接和挂载点管理器 - 统一管理在线挂载点、用户连接和STR表"""
    
    def __init__(self):
        # 在线挂载点表: {mount_name: MountInfo}
        self.online_mounts: Dict[str, MountInfo] = {}
        # 在线用户表: {user_id: [connection_info, ...]}
        self.online_users = defaultdict(list)
        # 用户连接计数: {username: count}
        self.user_connection_count = defaultdict(int)
        # 挂载点计数: {mount_name: count}
        self.mount_connection_count = defaultdict(int)
        # 统计信息
        self.total_connections = 0
        self.rejected_connections = 0
        self.clients = {}  # 活跃客户端
        
        self.mount_lock = RLock()
        self.user_lock = RLock()
        
        
    def print_active_connections(self):
        """实时打印当前所有活跃的NTRIP连接信息"""
        with self.mount_lock:
            # print("\n=== 当前活跃的NTRIP连接状态 ===")
            # print(f"活跃挂载点总数: {len(self.online_mounts)}")
            
            # if not self.online_mounts:
            #     print("当前没有活跃的挂载点连接")
            # else:
            #     for mount_name, mount_info in self.online_mounts.items():
            #         uptime = mount_info.uptime
            #         print(f"挂载点: {mount_name} | IP: {mount_info.ip_address} | 连接时长: {uptime:.1f}秒 | 状态: {mount_info.status}")
            
            # print(f"活跃用户连接总数: {len(self.online_users)}")
            # for username, connections in self.online_users.items():
            #     for conn_info in connections:
            #         print(f"用户: {username} | 连接ID: {conn_info.get('connection_id', 'N/A')} | IP: {conn_info.get('ip_address', 'N/A')} | 挂载点: {conn_info.get('mount_name', 'N/A')}")
            
            # 打印详细的连接统计
            # print(f"总连接数: {self.total_connections}, 拒绝连接数: {self.rejected_connections}")
            
            # print("=== 连接状态打印完毕 ===\n")
            pass
    
    def force_refresh_connections(self):
        """强制刷新连接状态并打印详细信息"""
        # print("\n=== 强制刷新连接状态 ===")
        # print(f"当前时间: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        # 检查挂载点连接的有效性
        invalid_mounts = []
        for mount_name, mount_info in self.online_mounts.items():
            idle_time = mount_info.idle_time
            if idle_time > 60:  # 超过60秒没有数据
                invalid_mounts.append(mount_name)
                # print(f">>> 警告: 挂载点 {mount_name} 已空闲 {idle_time:.1f}秒")
        self.print_active_connections()
    
    def cleanup_zombie_connections(self):
        """清理僵尸连接 - 检查系统层面socket状态"""
        import subprocess
        import re
        
        try:
            # 获取系统层面的socket连接状态
            result = subprocess.run(['netstat', '-an'], capture_output=True, text=True, shell=True)
            if result.returncode != 0:
                log_warning("无法获取系统socket状态")
                return
            
            # 解析ESTABLISHED连接
            established_ips = set()
            for line in result.stdout.split('\n'):
                if ':2101' in line and 'ESTABLISHED' in line:
                    # 提取远程IP地址
                    match = re.search(r'(\d+\.\d+\.\d+\.\d+):(\d+)\s+ESTABLISHED', line)
                    if match:
                        remote_ip = match.group(1)
                        established_ips.add(remote_ip)
            
            # 检查应用层连接状态
            with self.mount_lock:
                zombie_mounts = []
                for mount_name, mount_info in self.online_mounts.items():
                    if mount_info.ip_address not in established_ips:
                        zombie_mounts.append(mount_name)
                        log_warning(f"检测到僵尸连接: 挂载点 {mount_name}, IP {mount_info.ip_address}")
                
                # 清理僵尸连接
                for mount_name in zombie_mounts:
                    log_info(f"清理僵尸连接: {mount_name}")
                    self.remove_mount_connection(mount_name, "僵尸连接清理")
                
                if zombie_mounts:
                    log_info(f"已清理 {len(zombie_mounts)} 个僵尸连接")
                else:
                    log_debug("未发现僵尸连接")
                    
        except Exception as e:
            log_error(f"清理僵尸连接时发生异常: {e}", exc_info=True)

    def cleanup_stale_user_connections(self, max_idle_seconds=300, force_check=False):
        """清理空闲过久的用户连接（防止 keep-alive 未检测到的断开）

        当客户端因网络问题（4G 抖动、NAT 超时等）悄悄断开时，TCP 层可能不发送 FIN/RST，
        服务端的 keep-alive 也可能检测不到（半开连接），导致用户卡在 online_users 状态、
        mount_offline 事件永远丢失。

        本方法在 force_check=True 时主动探测 socket（MSG_PEEK），否则用最近活动时间判断。
        """
        import select as _select
        with self.user_lock:
            current_time = time.time()
            stale = []  # (username, indices) 待删除

            for username, conns in list(self.online_users.items()):
                stale_indices = []
                for i, conn in enumerate(conns):
                    last_activity = conn.get('last_activity', conn.get('connect_time', current_time))
                    idle_seconds = current_time - last_activity
                    if idle_seconds < max_idle_seconds:
                        continue

                    sock = conn.get('client_socket')
                    should_remove = False
                    if sock is None:
                        should_remove = True
                    elif force_check:
                        try:
                            r, _, _ = _select.select([sock], [], [], 0.0)
                            if r:
                                try:
                                    data = sock.recv(1, socket.MSG_PEEK)
                                    if not data:
                                        should_remove = True
                                except (BlockingIOError, InterruptedError):
                                    pass
                                except OSError:
                                    should_remove = True
                        except (OSError, ValueError):
                            should_remove = True
                    if should_remove:
                        stale_indices.append(i)

                if stale_indices:
                    stale.append((username, stale_indices))

            cleaned = 0
            for username, indices in stale:
                if username not in self.online_users:
                    continue
                for i in reversed(indices):
                    if i >= len(self.online_users[username]):
                        continue
                    conn = self.online_users[username][i]
                    uptime = round(current_time - conn.get('connect_time', current_time), 1)
                    # 记录离线事件（用独立 try 避免一处异常影响其他）
                    try:
                        notification.notify_mount_offline(
                            conn['mount_name'],
                            conn['ip_address'],
                            username=username,
                            connect_time=conn.get('connect_datetime', '未知'),
                            uptime=uptime,
                            reason="超时清理"
                        )
                    except Exception as e:
                        log_error(f"超时清理通知失败: {e}")
                    try:
                        database.add_connection_event(
                            event_type='mount_offline',
                            mount_name=conn['mount_name'],
                            username=username,
                            ip_address=conn['ip_address'],
                            duration=uptime,
                            reason="超时清理",
                            details=f"连接时间: {conn.get('connect_datetime', '未知')}, 空闲 {int(current_time - conn.get('last_activity', current_time))}s"
                        )
                    except Exception as e:
                        log_error(f"超时清理事件记录失败: {e}")

                    # 关闭 socket
                    sock = conn.get('client_socket')
                    if sock is not None:
                        try:
                            sock.close()
                        except Exception:
                            pass

                    # 维护计数
                    mname = conn.get('mount_name')
                    if mname in self.mount_connection_count:
                        self.mount_connection_count[mname] -= 1
                    del self.online_users[username][i]
                    if username in self.user_connection_count:
                        self.user_connection_count[username] -= 1
                    cleaned += 1
                    log_info(f"超时清理用户连接: {username}@{mname}, idle={int(current_time - conn.get('last_activity', current_time))}s")

                if username in self.online_users and not self.online_users[username]:
                    del self.online_users[username]
                    if username in self.user_connection_count:
                        del self.user_connection_count[username]

            if cleaned:
                log_info(f"超时清理完成，共清理 {cleaned} 个用户连接")
            return cleaned

    def add_mount_connection(self, mount_name, ip_address, user_agent="", protocol_version="1.0", client_socket=None):
        """添加挂载点连接上传端"""
        with self.mount_lock:
            if mount_name in self.online_mounts:
                log_debug(f"挂载点 {mount_name} 仍在线程表中，可能是相同IP重复连接的清理过程")
               
                del self.online_mounts[mount_name]
            
            log_debug(f"开始创建挂载点连接 - 名称: {mount_name}, IP: {ip_address}, User-Agent: {user_agent}, 协议版本: {protocol_version}")
            
            # 创建挂载点信息
            mount_info = MountInfo(
                mount_name=mount_name,
                ip_address=ip_address,
                user_agent=user_agent,
                protocol_version=protocol_version,
                client_socket=client_socket
            )
            
            # 添加到在线挂载点表
            self.online_mounts[mount_name] = mount_info
            log_debug(f"挂载点 {mount_name} 已添加到在线列表，当前在线挂载点数量: {len(self.online_mounts)}")
            
            # 生成初始STR表
            self._generate_initial_str(mount_name)
            
            # 启动STR修正解析流程
            self.start_str_correction(mount_name)
            
            log_info(f"挂载点 {mount_name} 已上线，IP: {ip_address}当前在线挂载点数量: {len(self.online_mounts)}")
            log_debug(f"挂载点 {mount_name} 连接成功，初始状态: {mount_info.status}, 连接时间: {mount_info.connect_datetime}")
            
            # 触发基站/挂载点上传端上线通知
            notification.notify_base_station_online(mount_name, ip_address, mount_info.connect_datetime)
            
            # 记录基站上线事件到数据库
            database.add_connection_event(
                event_type='base_station_online',
                mount_name=mount_name,
                ip_address=ip_address,
                details=f'首次连接时间: {mount_info.connect_datetime}'
            )
            
            self.print_active_connections()
            
            return True, "Mount point connected successfully"
    
    def remove_mount_connection(self, mount_name, reason="主动断开"):
        """移除挂载点连接（上传端断开）"""
        with self.mount_lock:
            if mount_name in self.online_mounts:
                mount_info = self.online_mounts[mount_name]
                
                # 强制关闭socket
                if mount_info.client_socket:
                    try:
                        mount_info.client_socket.close()
                        log_info(f"已强制关闭挂载点 {mount_name} 的socket连接")
                    except Exception as e:
                        log_warning(f"关闭挂载点 {mount_name} socket连接失败: {e}")
                
                # 记录断开信息
                log_debug(f"挂载点 {mount_name} 已断开 详情: {reason}, 状态: {mount_info.status}, 总字节数: {mount_info.total_bytes}, 数据速率: {mount_info.data_rate:.2f} B/s")
                log_debug(f"挂载点 {mount_name} 统计信息 - 总消息数: {mount_info.total_messages}, 数据包数: {mount_info.data_count}, 空闲时间: {mount_info.idle_time:.1f}秒")
                
                # 判断断开原因 用于调试
                if mount_info.status == "online":
                    actual_reason = reason if reason != "主动断开" else "正常断开"
                else:
                    actual_reason = "异常离线"
                
                del self.online_mounts[mount_name]
                
                # 触发基站/挂载点上传端下线通知
                notification.notify_base_station_offline(
                    mount_name,
                    mount_info.ip_address,
                    mount_info.connect_datetime,
                    uptime=round(mount_info.uptime, 1),
                    reason=actual_reason
                )
                
                # 记录基站下线事件到数据库
                database.add_connection_event(
                    event_type='base_station_offline',
                    mount_name=mount_name,
                    ip_address=mount_info.ip_address,
                    duration=round(mount_info.uptime, 1),
                    reason=actual_reason,
                    details=f'连接时间: {mount_info.connect_datetime}, 总字节数: {mount_info.total_bytes}, 数据速率: {mount_info.data_rate:.2f} B/s'
                )
                
                log_info(f"挂载点 {mount_name} 已下线，连接时长: {mount_info.uptime:.1f}秒，原因: {actual_reason}")
                log_debug(f"挂载点 {mount_name} 移除完成，剩余在线挂载点数量: {len(self.online_mounts)}")
                self.print_active_connections()
                
                return True
            else:
                log_debug(f"尝试移除不存在的挂载点: {mount_name}")
                return False
    
    def _generate_initial_str(self, mount_name: str):
        """生成初始STR表"""
        parse_result = {}  
        self._process_str_data(mount_name, parse_result, mode="initial")
    def _update_message_statistics(self, mount_name: str, parsed_messages, data_size: int) -> bool:
        """更新挂载点基本统计信息"""
        if mount_name not in self.online_mounts:
            log_debug(f"统计更新失败 - 挂载点 {mount_name} 不在线")
            return False
        
        mount_info = self.online_mounts[mount_name]
        current_time = time.time()
        old_total_bytes = mount_info.total_bytes
        old_data_rate = mount_info.data_rate
        
        with self.mount_lock:
            
            mount_info.last_update = current_time
            mount_info.last_data_time = current_time
            mount_info.total_bytes += data_size
            mount_info.data_count += 1
            
            uptime = mount_info.uptime
            if uptime > 0:
                mount_info.data_rate = mount_info.total_bytes / uptime
            
            # 移除频繁的统计更新debug日志，避免刷屏
            # log_debug(f"挂载点 {mount_name} 统计更新: 数据包大小={data_size}B, 累计字节={mount_info.total_bytes}B (增加{data_size}B), 数据速率={mount_info.data_rate:.2f}B/s (原{old_data_rate:.2f}B/s)")
            # log_debug(f"挂载点 {mount_name} 计数更新: 数据包数={mount_info.data_count}, 运行时间={uptime:.1f}秒, 空闲时间={mount_info.idle_time:.1f}秒")
        
        return True
    
    def update_mount_data(self, mount_name: str, data_size: int) -> bool:
        """更新挂载点数据统计"""
        if mount_name not in self.online_mounts:
            return False
        
        return self._update_message_statistics(mount_name, None, data_size)
    
    def get_mount_str_data(self, mount_name: str) -> Optional[str]:
        """获取挂载点的STR表数据"""
        if mount_name in self.online_mounts:
            return self.online_mounts[mount_name].str_data
        return None
    
    def get_all_str_data(self) -> Dict[str, str]:
        """获取所有挂载点的STR表数据"""
        str_data = {}
        with self.mount_lock:
            for mount_name, mount_info in self.online_mounts.items():
                if mount_info.str_data:
                    str_data[mount_name] = mount_info.str_data
        return str_data

    
    def add_user_connection(self, username, mount_name, ip_address, user_agent="", protocol_version="1.0", client_socket=None):
        """添加用户连接"""
        with self.user_lock:
            connection_id = f"{username}_{mount_name}_{int(time.time())}"
            
            socket_info = "无socket" if client_socket is None else f"端口:{getattr(client_socket, 'getpeername', lambda: ('未知', '未知'))()[1] if hasattr(client_socket, 'getpeername') else '未知'}"
            log_debug(f"创建用户连接 - 用户: {username}, 挂载点: {mount_name}, IP: {ip_address}, {socket_info}, User-Agent: {user_agent}")
            
            connection_info = {
                'connection_id': connection_id,
                'username': username,
                'mount_name': mount_name,
                'ip_address': ip_address,
                'user_agent': user_agent,
                'protocol_version': protocol_version,
                'connect_time': time.time(),
                'connect_datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'last_activity': time.time(),
                'bytes_sent': 0,
                'diff_start_time': None,
                'gga_quality': None,
                'gga_last_update': None,
                'client_socket': client_socket
            }
            
            if username not in self.online_users:
                self.online_users[username] = []
                log_debug(f"为新用户 {username} 创建连接列表")
            if username not in self.user_connection_count:
                self.user_connection_count[username] = 0
            if mount_name not in self.mount_connection_count:
                self.mount_connection_count[mount_name] = 0
            
            old_user_count = self.user_connection_count[username]
            old_mount_count = self.mount_connection_count[mount_name]
            
            self.online_users[username].append(connection_info)
            self.user_connection_count[username] += 1
            self.mount_connection_count[mount_name] += 1
            
            log_info(f"用户 {username} IP: {ip_address} 已连接，从挂载点 {mount_name}开始订阅RTCM数据")
            log_debug(f"用户连接统计更新 - 用户 {username}: {old_user_count} -> {self.user_connection_count[username]}, 挂载点 {mount_name}: {old_mount_count} -> {self.mount_connection_count[mount_name]}")
            log_debug(f"连接ID生成: {connection_id}, 总在线用户数: {len(self.online_users)}")
            
            # 触发用户挂载点连接上线通知
            notification.notify_mount_online(
                mount_name,
                ip_address,
                username=username,
                connect_time=connection_info['connect_datetime']
            )
            
            # 记录用户挂载点连接上线事件到数据库
            database.add_connection_event(
                event_type='mount_online',
                mount_name=mount_name,
                username=username,
                ip_address=ip_address,
                details=f"连接时间: {connection_info['connect_datetime']}"
            )
            
            return connection_id
    
    def remove_user_connection(self, username, connection_id=None, mount_name=None, reason="主动断开"):
        """移除用户连接"""
        with self.user_lock:
            if username not in self.online_users:
                return False

            connections_to_remove = []

            for i, conn in enumerate(self.online_users[username]):
                should_remove = False
                
                if connection_id and conn['connection_id'] == connection_id:
                    should_remove = True
                elif mount_name and conn['mount_name'] == mount_name:
                    should_remove = True
                elif not connection_id and not mount_name:
                    should_remove = True
                
                if should_remove:
                    connections_to_remove.append(i)
                    self.mount_connection_count[conn['mount_name']] -= 1
                    
                    
                    if conn.get('client_socket'):
                        try:
                            conn['client_socket'].close()
                        except:
                            pass
                    
                    # 触发用户挂载点连接下线通知
                    uptime = round(time.time() - conn.get('connect_time', time.time()), 1)
                    notification.notify_mount_offline(
                        conn['mount_name'],
                        conn['ip_address'],
                        username=username,
                        connect_time=conn.get('connect_datetime', '未知'),
                        uptime=uptime,
                        reason=reason
                    )

                    # 记录用户挂载点连接下线事件到数据库
                    database.add_connection_event(
                        event_type='mount_offline',
                        mount_name=conn['mount_name'],
                        username=username,
                        ip_address=conn['ip_address'],
                        duration=uptime,
                        reason=reason,
                        details=f"连接时间: {conn.get('connect_datetime', '未知')}"
                    )

                    log_info(f"用户 {username} 已从挂载点 {conn['mount_name']} 断开（{reason}）")
            

            for i in reversed(connections_to_remove):
                del self.online_users[username][i]
                self.user_connection_count[username] -= 1
            
            if not self.online_users[username]:
                del self.online_users[username]
                del self.user_connection_count[username]
            
            return len(connections_to_remove) > 0
    
    def update_mount_data_stats(self, mount_name, data_size):
        """更新挂载点数据统计"""
        if mount_name in self.online_mounts:
            mount_info = self.online_mounts[mount_name]
            mount_info.data_count += 1
            mount_info.last_data_time = time.time()
            mount_info.total_bytes += data_size
            uptime = mount_info.uptime
            if uptime > 0:
                mount_info.data_rate = mount_info.total_bytes / uptime
    
    def update_user_activity(self, username, connection_id, bytes_sent=0):
        """更新用户状态"""
        with self.user_lock:
            if username not in self.online_users:
                log_debug(f"用户状态更新失败 - 用户 {username} 不在线")
                return False
            
            connection_found = False
            for conn in self.online_users[username]:
                if conn['connection_id'] == connection_id:
                    old_bytes = conn['bytes_sent']
                    conn['last_activity'] = time.time()
                    conn['bytes_sent'] += bytes_sent
                    if old_bytes == 0 and bytes_sent > 0 and conn['diff_start_time'] is None:
                        conn['diff_start_time'] = time.time()
                    
                    # 实时计算并存储数据速率 (B/s)
                    diff_start = conn.get('diff_start_time') or conn['connect_time']
                    elapsed = time.time() - diff_start
                    if elapsed > 0:
                        conn['data_rate'] = conn['bytes_sent'] / elapsed
                    else:
                        conn['data_rate'] = 0.0
                    
                    connection_found = True
                    break
            
            if not connection_found:
                log_debug(f"用户状态更新失败 - 连接ID {connection_id} 不存在于用户 {username}")
                return False
            
            return True
    
    def update_gga_quality(self, username, connection_id, quality):
        """更新用户连接的GGA定位质量码"""
        with self.user_lock:
            if username not in self.online_users:
                return False
            
            for conn in self.online_users[username]:
                if conn['connection_id'] == connection_id:
                    conn['gga_quality'] = quality
                    conn['gga_last_update'] = time.time()
                    return True
            return False
    
    def get_user_connection_gga_quality(self, username, connection_id):
        """获取用户连接的GGA定位质量码"""
        with self.user_lock:
            if username not in self.online_users:
                return None
            
            for conn in self.online_users[username]:
                if conn['connection_id'] == connection_id:
                    quality = conn.get('gga_quality')
                    # Check if GGA data is stale (older than 30 seconds)
                    last_update = conn.get('gga_last_update')
                    if quality is not None and last_update is not None:
                        if time.time() - last_update <= 30:
                            return quality
                    return None
            return None
    def is_mount_online(self, mount_name):
        """检查挂载点是否在线"""
        with self.mount_lock:
            return mount_name in self.online_mounts
    
    def get_user_connection_count(self, username):
        """获取用户连接数"""
        return self.user_connection_count.get(username, 0)
    
    def get_user_connect_time(self, username):
        """获取用户最新连接时间"""
        with self.user_lock:
            if username in self.online_users and self.online_users[username]:
                
                latest_connection = max(self.online_users[username], key=lambda x: x['connect_time'])
                return latest_connection['connect_datetime']
            return None
    
    def get_mount_connection_count(self, mount_name):
        """获取挂载点连接数"""
        return self.mount_connection_count.get(mount_name, 0)
    
    def get_online_mounts(self):
        """获取在线挂载点列表"""
        with self.mount_lock:
            return {name: info.to_dict() for name, info in self.online_mounts.items()}
    
    def get_online_users(self):
        """获取在线用户列表"""
        with self.user_lock:
            return dict(self.online_users)

    def get_online_users_api(self) -> Dict[str, list]:
        """获取在线用户列表（API/推送安全副本，剥离开client_socket避免JSON序列化失败）"""
        with self.user_lock:
            users = {}
            for username, conn_list in self.online_users.items():
                safe_list = []
                for conn in conn_list:
                    c = conn.copy()
                    c.pop('client_socket', None)
                    safe_list.append(c)
                users[username] = safe_list
            return users
    
    def get_mount_info(self, mount_name):
        """获取挂载点信息"""
        if mount_name in self.online_mounts:
            return self.online_mounts[mount_name].to_dict()
        return None
    
    def get_user_connections(self, username):
        """获取用户连接信息"""
        return self.online_users.get(username, [])
    
    def get_all_user_connections(self, mount_name=None, username=None):
        """获取所有移动站连接列表，支持按挂载点和用户名筛选"""
        with self.user_lock:
            connections = []
            for user, conn_list in self.online_users.items():
                for conn in conn_list:
                    if mount_name and conn.get('mount_name') != mount_name:
                        continue
                    if username and conn.get('username') != username:
                        continue
                    conn_copy = conn.copy()
                    conn_copy.pop('client_socket', None)
                    connections.append(conn_copy)
            return connections

    def get_all_user_connections_enriched(self, mount_filter=None, username_filter=None):
        """获取所有移动站连接列表（一次遍历一次性计算所有派生字段，避免逐连接重复加锁）

        返回每条连接附带：is_diffing、data_rate、gga_quality
        """
        diffing_timeout = getattr(config, 'DIFFING_TIMEOUT', 10)
        now = time.time()

        with self.user_lock:
            result = []
            for user, conn_list in self.online_users.items():
                if username_filter and username_filter.lower() not in user.lower():
                    continue
                for conn in conn_list:
                    if mount_filter and mount_filter.lower() not in conn.get('mount_name', '').lower():
                        continue

                    bytes_sent = conn.get('bytes_sent', 0) or 0
                    last_activity = conn.get('last_activity')
                    is_diffing = bool(
                        bytes_sent > 0
                        and last_activity is not None
                        and (now - last_activity) <= diffing_timeout
                    )

                    diff_start = conn.get('diff_start_time') or conn.get('connect_time') or now
                    elapsed = now - diff_start
                    data_rate = (bytes_sent / elapsed) if elapsed > 0 else 0.0

                    quality = conn.get('gga_quality')
                    gga_last_update = conn.get('gga_last_update')
                    gga_quality = None
                    if quality is not None and gga_last_update is not None:
                        if now - gga_last_update <= 30:
                            gga_quality = quality

                    result.append({
                        'connection_id': conn.get('connection_id', ''),
                        'username': user,
                        'mount_name': conn.get('mount_name', ''),
                        'ip_address': conn.get('ip_address', ''),
                        'connect_time': conn.get('connect_datetime', '-'),
                        'bytes_sent': bytes_sent,
                        'data_rate': round(data_rate, 3),
                        'is_diffing': is_diffing,
                        'gga_quality': gga_quality
                    })
            return result

    def is_user_connection_diffing(self, username, connection_id):
        """判断用户连接是否正在进行差分
        
        规则：连接存在、已经发送过数据（bytes_sent > 0）、
              且最后一次数据发送活动在最近 DIFFING_TIMEOUT 秒内
        """
        diffing_timeout = getattr(config, 'DIFFING_TIMEOUT', 10)
        
        with self.user_lock:
            if username not in self.online_users:
                return False
            for conn in self.online_users[username]:
                if conn['connection_id'] == connection_id:
                    if conn['bytes_sent'] <= 0:
                        return False
                    if conn['last_activity'] is None:
                        return False
                    return time.time() - conn['last_activity'] <= diffing_timeout
            return False

    def get_user_connection_data_rate(self, username, connection_id):
        """获取用户连接的当前数据速率（B/s）"""
        with self.user_lock:
            if username not in self.online_users:
                return 0.0
            for conn in self.online_users[username]:
                if conn['connection_id'] == connection_id:
                    diff_start = conn.get('diff_start_time') or conn['connect_time']
                    elapsed = time.time() - diff_start
                    if elapsed > 0:
                        return conn['bytes_sent'] / elapsed
                    return 0.0
            return 0.0

    def get_user_connection_mounts(self, username):
        """获取用户当前连接的所有挂载点（去重）"""
        with self.user_lock:
            if username not in self.online_users:
                return []
            mounts = set()
            for conn in self.online_users[username]:
                mounts.add(conn['mount_name'])
            return sorted(mounts)

    def get_all_online_mount_names(self):
        """获取所有在线挂载点名称列表"""
        with self.mount_lock:
            return sorted(self.online_mounts.keys())
    
    def get_mount_statistics(self, mount_name: str) -> Optional[Dict[str, Any]]:
        """获取挂载点统计信息"""
        if mount_name not in self.online_mounts:
            return None
        
        mount_info = self.online_mounts[mount_name]
        return {
            'mount_name': mount_name,
            'status': mount_info.status,
            'uptime': mount_info.uptime,
            'total_bytes': mount_info.total_bytes,
            'data_rate': mount_info.data_rate,
            'data_count': mount_info.data_count
        }
    
    def generate_mount_list(self):
        """生成挂载点列表数据"""
        mount_list = []
        
        with self.mount_lock:
            for mount_name, mount_info in self.online_mounts.items():
               
                if mount_info.str_data:
                    mount_list.append(mount_info.str_data)

                else:
                    # 生成默认的NTRIP格式信息
                    mount_data = [
                        'STR',
                        mount_name, #挂载点名称
                        'none',  # 城市名称或其他描述 默认none
                        'RTCM 3.3',  # format
                        '1005(10)',  # format_details
                        '0',  # carrier
                        'GPS',  # nav_system
                        '2RTK',  # network
                        'CHN',  # country
                        str(mount_info.lat) if mount_info.lat is not None else '0.0',  # latitude
                        str(mount_info.lon) if mount_info.lon is not None else '0.0',  # longitude
                        '0',  # nmea
                        '0',  # solution
                        mount_info.user_agent or 'unknown',  # generator
                        'N',  # compression
                        'B',  # authentication
                        'N',  # fee
                        '500',  # bitrate
                        'NO'  # misc
                    ]
                    mount_info_str = ';'.join(mount_data)
                    mount_list.append(mount_info_str)
                    log_info(f"已为挂载点 {mount_name} 创建STR: {mount_info_str}", 'connection_manager')
        
        return mount_list
    
    def get_statistics(self):
        """获取总体统计信息"""
        with self.mount_lock, self.user_lock:
            total_mounts = len(self.online_mounts)
            total_users = sum(len(connections) for connections in self.online_users.values())
            
            mount_stats = []
            for mount_name, mount_info in self.online_mounts.items():
                mount_stats.append({
                    'mount_name': mount_name,
                    'ip_address': mount_info.ip_address,
                    'uptime': mount_info.uptime,
                    'data_count': mount_info.data_count,
                    'total_bytes': mount_info.total_bytes,
                    'total_messages': mount_info.total_messages,
                    'data_rate': mount_info.data_rate,
                    'user_count': self.mount_connection_count.get(mount_name, 0),
                    'status': mount_info.status,
                    'str_generated': mount_info.final_str_generated
                })
            
            user_stats = []
            for username, connections in self.online_users.items():
                for conn in connections:
                    user_stats.append({
                        'username': username,
                        'mount_name': conn['mount_name'],
                        'ip_address': conn['ip_address'],
                        'connect_time': conn['connect_time'],
                        'bytes_sent': conn['bytes_sent']
                    })
            
            return {
                'total_mounts': total_mounts,
                'total_users': total_users,
                'mounts': mount_stats,
                'users': user_stats
            }
    
    def start_str_correction(self, mount_name: str):
        """启动30秒RTCM解析并修正STR"""
        if mount_name not in self.online_mounts:
            log_warning(f"无法启动STR修正，挂载点 {mount_name} 不在线")
            return

        success = rtcm_manager.start_parser(
            mount_name=mount_name,
            mode="str_fix",
            duration=30
        )
        
        if not success:
            log_error(f"启动STR修正解析失败 [挂载点: {mount_name}]")
            return
            
        log_info(f"已启动STR修正解析 [挂载点: {mount_name}]，将在30秒后修正STR表")
        
        
        def wait_and_correct():
            log_debug(f"开始等待STR修正完成 [挂载点: {mount_name}]")
            time.sleep(35)  
            log_debug(f"等待完成，开始获取解析结果 [挂载点: {mount_name}]")
            
            parse_result = rtcm_manager.get_result(mount_name)
            log_debug(f"获取到解析结果 [挂载点: {mount_name}]: {parse_result is not None}")
            
            if parse_result:
                log_debug(f"解析结果内容 [挂载点: {mount_name}]: {parse_result}")
                
                self._process_str_data(mount_name, parse_result, mode="correct")
            else:
                log_warning(f"未获取到STR修正解析结果 [挂载点: {mount_name}]")
                log_debug(f"STR修正失败 - 挂载点: {mount_name}, 可能原因: 解析超时、数据不足或解析器异常")
            
            
            log_debug(f"停止解析器 [挂载点: {mount_name}]")
            rtcm_manager.stop_parser(mount_name)
            log_debug(f"STR修正流程完成 [挂载点: {mount_name}]")
        
        threading.Thread(target=wait_and_correct, daemon=True).start()

    def _process_str_data(self, mount_name: str, parse_result: dict, mode: str = "correct"):
        """统一的STR处理函数：支持初始生成、修正和重新生成模式
        
        Args:
            mount_name: 挂载点名称
            parse_result: 解析结果字典
            mode: 处理模式 - "initial"(初始生成), "correct"(修正), "regenerate"(重新生成)
        """
        log_debug(f"开始STR处理 [挂载点: {mount_name}, 模式: {mode}]")
        log_debug(f"解析结果详情: {parse_result}")
        
        with self.mount_lock:
           
            if mount_name not in self.online_mounts:
                log_debug(f"挂载点 {mount_name} 不在线，无法处理STR")
                return
            
            mount_info = self.online_mounts[mount_name]
            original_str = mount_info.str_data
            
            if mode == "initial":
                
                str_parts = self._create_initial_str_parts(mount_name, parse_result)
            elif mode in ["correct", "regenerate"]:
                
                if not original_str:
                    log_warning(f"挂载点 {mount_name} 无初始STR数据，切换到初始生成模式")
                    str_parts = self._create_initial_str_parts(mount_name, parse_result)
                else:
                    log_debug(f"原始STR [挂载点: {mount_name}]: {original_str}")
                    str_parts = original_str.split(';')
                    if len(str_parts) < 19:
                        log_error(f"STR格式错误，无法处理 [挂载点: {mount_name}] - 字段数量: {len(str_parts)}, 期望: 19")
                        return
                    
                    self._update_str_fields(str_parts, parse_result, mode)
            else:
                log_error(f"未知的STR处理模式: {mode}")
                return
            
            processed_str = ";".join(str_parts)
            log_debug(f"处理后STR [挂载点: {mount_name}]: {processed_str}")
           
            
            mount_info.str_data = processed_str
            if mode == "initial":
                mount_info.initial_str_generated = True
            else:
                mount_info.final_str_generated = True
            
            if mode == "correct":
                if original_str != processed_str:
                    log_info(f"{mount_name}已修正STR: {processed_str}")

                else:
                    log_info(f"STR表修正完成 [挂载点: {mount_name}]，无需更新")
                    log_info(f"当前STR: {processed_str}")
            elif mode == "initial":
                log_info(f"[挂载点: {mount_name}]STR已生成: {processed_str}")
            
            log_debug(f"STR处理流程结束 [挂载点: {mount_name}]，模式: {mode}, 最终状态: final_str_generated={mount_info.final_str_generated}")
    
    
    def _create_initial_str_parts(self, mount_name: str, parse_result: dict) -> list:
        """创建初始STR字段列表"""
        from . import config
        
        mount_info = self.online_mounts[mount_name]
        app_author = config.APP_AUTHOR.replace(' ', '') if config.APP_AUTHOR else '2rtk'
        
        identifier = parse_result.get("city") or mount_info.city or "none"
        country_code = parse_result.get("country") or mount_info.country or config.CASTER_COUNTRY
        latitude = parse_result.get("lat") or config.CASTER_LATITUDE
        longitude = parse_result.get("lon") or config.CASTER_LONGITUDE

        str_parts = [
            "STR",                          # 0: type
            mount_name,                     # 1: mountpoint
            identifier,                     # 2: identifier
            "RTCM3.x",                     # 3: format
            parse_result.get("message_types_str", "1005"),  # 4: format-details
            "0",                           # 5: 这里我不在使用RTCM标准，使用统计后的频段信息
            parse_result.get("gnss_combined", "GPS"),       # 6: nav-system
            app_author,                     # 7: network
            country_code,                   # 8: country
            f"{latitude:.4f}",             # 9: latitude
            f"{longitude:.4f}",            # 10: longitude
            "0",                           # 11: nmea
            "0",                           # 12: solution
            "2RTK_NtirpCaster",           # 13: generator
            "N",                           # 14: compression
            "B",                           # 15: authentication
            "N",                           # 16: fee
            "500",                         # 17: bitrate (这里RTCM.py中的计算方式有问题，后续要修改)
            "NO"                           # 18: misc
        ]
        
        self._update_str_fields(str_parts, parse_result, "initial")
        
        return str_parts
    
    def _update_str_fields(self, str_parts: list, parse_result: dict, mode: str = "correct"):
        """根据解析结果更新STR字段
        
        Args:
            str_parts: STR字段列表
            parse_result: 解析结果字典
            mode: 处理模式 - "initial"(初始生成), "correct"(修正), "regenerate"(重新生成)
        """
       
        if parse_result.get("city"):
            str_parts[2] = parse_result["city"]
        
       
        if parse_result.get("message_types_str"):
            str_parts[4] = parse_result["message_types_str"]
        
        
        if parse_result.get("carrier_combined"):
            carrier_info = parse_result["carrier_combined"]
            
            str_parts[5] = carrier_info  # 直接填入载波相位信息，如L1、L1+L5+B1等.不遵守RTCM标准.
            #标准的STR格式参考 https://software.rtcm-ntrip.org/wiki/STR
        
        # 4. 更新nav-system字段（第7个字段，导航系统）
        if parse_result.get("gnss_combined"):
            str_parts[6] = parse_result["gnss_combined"]
        
        # 5. 更新country字段（第9个字段，国家代码）
        if parse_result.get("country"):
            # rtcm2.py已经进行了2字符到3字符的转换，直接使用
            str_parts[8] = parse_result["country"]
        
        # 6. （第10个字段，纬度）
        if parse_result.get("lat"):
            str_parts[9] = f"{parse_result['lat']:.4f}"
        
        # 7. （第11个字段，经度）
        if parse_result.get("lon"):
            str_parts[10] = f"{parse_result['lon']:.4f}"
        
        # 8. （第14个字段）
        str_parts[13] = "2RTK_NtirpCaster"
        
        # 9. （第17个字段）
        str_parts[16] = "N"
        
        # 10.（第18个字段，比特率）
        if parse_result.get("bitrate"):
            bitrate_bps = parse_result["bitrate"]  
            str_parts[17] = str(int(bitrate_bps))  
        
        # 11. STR表最后一个字段.改为yes或no 用来判断STR是否修正
        if mode == "initial":
            str_parts[-1] = "NO"  # 初始生成时标记为未校验
        else:  # correct 或 regenerate 模式
            str_parts[-1] = "YES"  # 修正后标记为已校验

    def check_mount_exists(self, mount_name: str) -> bool:

        return mount_name in self.online_mounts
    

_connection_manager = None
_manager_lock = Lock()

#*****************************
# 连接管理 后期扩展使用 API管理 连接状态
#*****************************

def get_connection_manager():
    """获取全局连接管理器实例"""
    global _connection_manager
    if _connection_manager is None:
        with _manager_lock:
            if _connection_manager is None:
                _connection_manager = ConnectionManager()
    return _connection_manager

def add_mount_connection(mount_name, ip_address, user_agent="", protocol_version="1.0"):
    """添加挂载点连接"""
    return get_connection_manager().add_mount_connection(mount_name, ip_address, user_agent, protocol_version)

def remove_mount_connection(mount_name):
    """移除挂载点连接"""
    return get_connection_manager().remove_mount_connection(mount_name)

def add_user_connection(username, mount_name, ip_address, user_agent="", protocol_version="1.0", client_socket=None):
    """添加用户连接"""
    return get_connection_manager().add_user_connection(username, mount_name, ip_address, user_agent, protocol_version, client_socket)

def remove_user_connection(username, connection_id=None, mount_name=None, reason="主动断开"):
    """移除用户连接"""
    return get_connection_manager().remove_user_connection(username, connection_id=connection_id, mount_name=mount_name, reason=reason)

def update_user_activity(username, connection_id, bytes_sent=0):
    """更新用户活动"""
    return get_connection_manager().update_user_activity(username, connection_id, bytes_sent)

def is_mount_online(mount_name):
    """检查挂载点是否在线"""
    return get_connection_manager().is_mount_online(mount_name)

def get_user_connection_count(username):
    """获取用户连接数"""
    return get_connection_manager().get_user_connection_count(username)

def is_user_connection_diffing(username, connection_id):
    """判断用户连接是否正在差分"""
    return get_connection_manager().is_user_connection_diffing(username, connection_id)

def get_user_connection_data_rate(username, connection_id):
    """获取用户连接数据速率"""
    return get_connection_manager().get_user_connection_data_rate(username, connection_id)

def get_user_connection_mounts(username):
    """获取用户连接挂载点列表"""
    return get_connection_manager().get_user_connection_mounts(username)

def get_all_online_mount_names():
    """获取所有在线挂载点名称"""
    return get_connection_manager().get_all_online_mount_names()

def update_mount_data(mount_name, data_size):
    """更新挂载点数据"""
    return get_connection_manager().update_mount_data(mount_name, data_size)

def update_mount_data_stats(mount_name, data_size):
    """更新挂载点数据统计"""
    return get_connection_manager().update_mount_data_stats(mount_name, data_size)

def get_statistics():
    """获取统计信息"""
    return get_connection_manager().get_statistics()

def get_mount_statistics(mount_name):
    """获取挂载点统计信息"""
    return get_connection_manager().get_mount_statistics(mount_name)

def generate_mount_list():
    """生成挂载点列表数据"""
    return get_connection_manager().generate_mount_list()

def check_mount_exists(mount_name):
    """检查挂载点是否存在"""
    return get_connection_manager().check_mount_exists(mount_name)

def update_gga_quality(username, connection_id, quality):
    """更新用户连接GGA质量码"""
    return get_connection_manager().update_gga_quality(username, connection_id, quality)

def get_user_connection_gga_quality(username, connection_id):
    """获取用户连接GGA质量码"""
    return get_connection_manager().get_user_connection_gga_quality(username, connection_id)

