#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import socket
import threading
import queue
import re
from collections import deque
from threading import Lock, RLock
from . import config
from . import logger
from . import connection
from . import database

class RingBuffer:
    """环形缓冲区"""
    def __init__(self, maxlen=None):
        self.maxlen = maxlen or config.RING_BUFFER_SIZE
        self.buffer = deque(maxlen=self.maxlen)
        self.lock = Lock()
        self.total_bytes = 0
        self.total_messages = 0
        self.last_timestamp = time.time()
        self._write_index = 0
        self._read_index = 0
        
    def append(self, data, timestamp=None):
        """添加数据到环形缓冲区"""
        if timestamp is None:
            timestamp = time.time()
            
        with self.lock:
            item = {
                'data': data,
                'timestamp': timestamp,
                'size': len(data),
                'index': self._write_index
            }
            self.buffer.append(item)
            self.total_bytes += len(data)
            self.total_messages += 1
            self.last_timestamp = timestamp
            self._write_index += 1
                
    def get_since(self, timestamp):
        """获取指定时间戳之后的数据"""
        with self.lock:
            if not self.buffer:
                return []
            
            result = []
            for item in self.buffer:
                if item['timestamp'] > timestamp:
                    result.append((item['timestamp'], item['data']))
            
            return result
    
    def get_latest(self, count=None):
        """获取最新的数据"""
        with self.lock:
            if not self.buffer:
                return []
            
            if count is None:
                return [(item['timestamp'], item['data']) for item in self.buffer]
            else:
                items = list(self.buffer)[-count:] if count > 0 else []
                return [(item['timestamp'], item['data']) for item in items]
    
    def get_range(self, start_index, end_index=None):
        """根据索引范围获取数据"""
        with self.lock:
            if not self.buffer:
                return []
            
            result = []
            for item in self.buffer:
                if item['index'] >= start_index:
                    if end_index is None or item['index'] <= end_index:
                        result.append((item['timestamp'], item['data']))
            
            return result
    
    def get_stats(self):
        """获取缓冲区统计信息"""
        with self.lock:
            return {
                'size': len(self.buffer),
                'max_size': self.maxlen,
                'total_bytes': self.total_bytes,
                'total_messages': self.total_messages,
                'last_update': self.last_timestamp,
                'usage_percent': (len(self.buffer) / self.maxlen) * 100 if self.maxlen > 0 else 0,
                'write_index': self._write_index,
                'read_index': self._read_index
            }
    
    def clear(self):
        """清空缓冲区"""
        with self.lock:
            self.buffer.clear()
            self.total_bytes = 0
            self.total_messages = 0
            self._write_index = 0
            self._read_index = 0
    
    def is_full(self):
        """检查缓冲区是否已满"""
        with self.lock:
            return len(self.buffer) >= self.maxlen
    
    def is_empty(self):
        """检查缓冲区是否为空"""
        with self.lock:
            return len(self.buffer) == 0

class SimpleDataForwarder:
    """简化的数据广播"""
    
    def __init__(self, buffer_maxlen=None, broadcast_interval=None):
        self.buffer_maxlen = buffer_maxlen or config.RING_BUFFER_SIZE
        self.broadcast_interval = broadcast_interval or config.BROADCAST_INTERVAL
        
        self.mount_buffers = {}  # {mount_name: RingBuffer}
        self.buffer_lock = RLock()
        
        self.clients = {}  # {mount_name: [client_info]}
        self.client_lock = RLock()

        self.subscribers = {}  # {mount_name: [socket_write_end]}
        self.subscriber_lock = RLock()

        self.broadcast_thread = None
        self.running = False

        # GGA读取：使用单一selector线程复用所有客户端socket，避免每客户端一个线程
        self.gga_selector = None
        self.gga_buffers = {}      # {socket: bytes buffer}
        self.gga_reader_lock = RLock()
        self.gga_thread = None
        self.gga_running = False

        # 移动站 GGA 数据异步写库：GGA 读取线程仅入队，由独立线程批量入库，避免阻塞 IO 循环
        self.mobile_data_queue = queue.Queue(maxsize=20000)
        self.mobile_data_writer_thread = None
        self.mobile_data_writer_running = False
        # 批量写库参数：达到 FLUSH_BATCH 条 或 距上次写入超过 FLUSH_INTERVAL 秒 即触发
        self.MOBILE_DATA_FLUSH_BATCH = 200
        self.MOBILE_DATA_FLUSH_INTERVAL = 0.5

        self.stats = {
            'total_clients': 0,
            'active_clients': 0,
            'total_bytes_sent': 0,
            'total_messages_sent': 0,
            'failed_sends': 0,
            'disconnected_clients': 0
        }
    
    def start(self):
        """启动广播线程"""
        if self.running:
            return

        self.running = True
        self.broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.broadcast_thread.start()

        # 启动移动站 GGA 数据异步写库线程
        self._start_mobile_data_writer()

        logger.log_system_event('数据转发器已启动')

    def stop(self):
        """停止广播线程"""
        self.running = False

        if self.broadcast_thread and self.broadcast_thread.is_alive():
            self.broadcast_thread.join(timeout=5)

        # 停止GGA读取线程
        self._stop_gga_reader()

        # 停止移动站数据写库线程（会 flush 队列里残留的数据）
        self._stop_mobile_data_writer()

        # 关闭所有客户端连接
        with self.client_lock:
            for mount_clients in self.clients.values():
                for client_info in mount_clients[:]:
                    self._close_client(client_info)

        logger.log_system_event('数据转发器已停止')
    
    def add_client(self, client_socket, user, mount, agent, addr, protocol_version, connection_id=None):
        """添加客户端连接（同步方式）"""
        try:
            # 启用TCP Keep-Alive
            self._enable_keepalive(client_socket)
            
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            current_time = time.time()
            client_info = {
                'socket': client_socket,
                'user': user,
                'mount': mount,
                'agent': agent,
                'addr': addr,
                'protocol_version': protocol_version,
                'connection_id': connection_id,
                'connected_at': current_time,
                'last_seen': current_time,
                'last_sent_timestamp': current_time,  
                'bytes_sent': 0,
                'messages_sent': 0,
                'send_errors': 0,
                'gga_quality': None,
                'gga_last_update': None
            }
            
            with self.client_lock:
                # 限制同用户同挂载点的连接数
                if mount not in self.clients:
                    self.clients[mount] = []
                
                user_connections = [c for c in self.clients[mount] if c['user'] == user]
                if len(user_connections) >= config.MAX_USERS_PER_MOUNT:
                    
                    oldest = min(user_connections, key=lambda x: x['connected_at'])
                    self.remove_client(oldest, reason="超出每挂载点最大连接数")
                
                self.clients[mount].append(client_info)
                
                self.stats['total_clients'] += 1
                self.stats['active_clients'] = sum(len(clients) for clients in self.clients.values())
            
            logger.log_client_connect(user, mount, addr[0], protocol_version)
            
            # Start GGA reader thread for this client
            self._start_gga_reader(client_info)
            
            return client_info
            
        except Exception as e:
            logger.log_error(f"添加客户端失败: {e}", exc_info=True)
            try:
                client_socket.close()
            except:
                pass
            raise
    
    def _start_gga_reader(self, client_info):
        """注册客户端socket到单一线程的selector轮询（不再为每个客户端单独起线程）"""
        socket_obj = client_info.get('socket')
        if not socket_obj:
            return

        try:
            socket_obj.setblocking(False)
        except (OSError, ValueError):
            return

        with self.gga_reader_lock:
            if self.gga_selector is None:
                import selectors
                self.gga_selector = selectors.DefaultSelector()
            self.gga_selector.register(socket_obj, selectors.EVENT_READ, client_info)
            self.gga_buffers[socket_obj] = b''
            self._ensure_gga_reader()

    def _ensure_gga_reader(self):
        """确保单一线程的GGA读取循环已启动"""
        if self.gga_running:
            return
        self.gga_running = True
        self.gga_thread = threading.Thread(target=self._gga_reader_loop, daemon=True)
        self.gga_thread.start()

    def _stop_gga_reader(self):
        """停止GGA读取线程并清理selector"""
        self.gga_running = False
        if self.gga_thread and self.gga_thread.is_alive():
            self.gga_thread.join(timeout=2)
        if self.gga_selector is not None:
            try:
                self.gga_selector.close()
            except Exception:
                pass
            self.gga_selector = None
        self.gga_buffers.clear()

    def _gga_reader_loop(self):
        """单一GGA读取循环：用selector复用所有客户端socket"""
        import selectors
        while self.gga_running:
            try:
                if self.gga_selector is None:
                    time.sleep(0.2)
                    continue

                events = self.gga_selector.select(timeout=0.5)
                for key, _ in events:
                    socket_obj = key.fileobj
                    client_info = key.data
                    try:
                        data = socket_obj.recv(4096)
                        if not data:
                            # 客户端已断开 (EOF)，注销 selector 并触发完整的客户端清理
                            self._unregister_gga_socket(socket_obj)
                            try:
                                self.remove_client(client_info, reason="GGA 检测到 EOF")
                            except Exception as e:
                                logger.log_warning(f"GGA EOF 触发清理失败: {e}", 'ntrip')
                            continue
                    except BlockingIOError:
                        continue
                    except (OSError, ConnectionResetError, BrokenPipeError):
                        # socket 错误，注销 selector 并触发清理
                        self._unregister_gga_socket(socket_obj)
                        try:
                            self.remove_client(client_info, reason="GGA socket 错误")
                        except Exception as e:
                            logger.log_warning(f"GGA socket错误触发清理失败: {e}", 'ntrip')
                        continue
                    except Exception:
                        self._unregister_gga_socket(socket_obj)
                        try:
                            self.remove_client(client_info, reason="GGA 异常")
                        except Exception:
                            pass
                        continue

                    buffer = self.gga_buffers.get(socket_obj, b'') + data
                    if len(buffer) > 65536:
                        buffer = buffer[-65536:]

                    while b'\r\n' in buffer or b'\n' in buffer:
                        idx_crlf = buffer.find(b'\r\n')
                        idx_lf = buffer.find(b'\n')

                        if idx_crlf != -1 and (idx_lf == -1 or idx_crlf < idx_lf):
                            line = buffer[:idx_crlf].decode('utf-8', errors='ignore')
                            buffer = buffer[idx_crlf + 2:]
                        elif idx_lf != -1:
                            line = buffer[:idx_lf].decode('utf-8', errors='ignore')
                            buffer = buffer[idx_lf + 1:]
                        else:
                            break

                        quality = self._parse_gga_quality(line)
                        if quality is not None and client_info is not None:
                            client_info['gga_quality'] = quality
                            client_info['gga_last_update'] = time.time()
                            # Update connection manager
                            if client_info.get('connection_id') and client_info.get('user'):
                                try:
                                    connection.update_gga_quality(
                                        client_info['user'],
                                        client_info['connection_id'],
                                        quality
                                    )
                                except Exception:
                                    pass

                        # 入队保存到数据库（异步批量写库，不阻塞 IO 循环）
                        # 只要是 GGA 行就记录，quality 是否有效不影响入库
                        if self._is_gga_line(line) and client_info is not None:
                            try:
                                nmea_type_raw = line.split(',', 1)[0] if line else None
                                nmea_type = nmea_type_raw.lstrip('$') if nmea_type_raw else None
                                addr = client_info.get('addr')
                                ip_address = addr[0] if isinstance(addr, tuple) and addr else None
                                row = (
                                    None,  # event_time: None 让 DB 默认填充
                                    client_info.get('user'),
                                    client_info.get('mount'),
                                    ip_address,
                                    nmea_type,
                                    line,
                                    len(line),
                                )
                                self.mobile_data_queue.put_nowait(row)
                            except queue.Full:
                                # 队列满：丢弃最旧的数据并重试（保证新数据优先）
                                try:
                                    self.mobile_data_queue.get_nowait()
                                except Exception:
                                    pass
                                try:
                                    self.mobile_data_queue.put_nowait(row)
                                except Exception:
                                    pass
                            except Exception:
                                pass

                    self.gga_buffers[socket_obj] = buffer

            except Exception:
                # 避免selector单点异常导致整个读循环退出
                try:
                    time.sleep(0.2)
                except Exception:
                    break

        # 循环结束清理selector
        if self.gga_selector is not None:
            try:
                self.gga_selector.close()
            except Exception:
                pass

    def _unregister_gga_socket(self, socket_obj):
        """从selector注销客户端socket并清理缓冲区"""
        try:
            if self.gga_selector is not None:
                self.gga_selector.unregister(socket_obj)
        except Exception:
            pass
        try:
            self.gga_buffers.pop(socket_obj, None)
        except Exception:
            pass
    
    def _parse_gga_quality(self, line):
        """解析NMEA GGA消息，返回定位质量码

        GGA格式: $GNGGA,time,lat,N,lon,E,quality,sats,hdop,alt,M,geoid,M,age,ref*cs\r\n
        quality: 0=无效, 1=GPS单点, 2=DGPS, 4=RTK固定, 5=RTK浮点, 6=估算
        """
        if not line or not line.startswith('$G') or 'GGA' not in line:
            return None

        try:
            parts = line.split(',')
            if len(parts) < 7:
                return None

            # Check if it's a valid GGA message
            msg_type = parts[0]
            if not (msg_type.endswith('GGA') and msg_type.startswith('$G')):
                return None

            quality_str = parts[6]
            if quality_str == '' or quality_str is None:
                return None

            quality = int(quality_str)
            # Valid quality codes: 0-8
            if 0 <= quality <= 8:
                return quality
            return None
        except (ValueError, IndexError):
            return None

    def _is_gga_line(self, line):
        """判断是否为 GGA 行（不要求 quality 字段有效）

        与 _parse_gga_quality 相比：仅做结构判定，quality 字段是否合法不影响返回值。
        用于「数据查看」全量记录 GGA 报文。
        """
        if not line or not line.startswith('$G'):
            return False
        parts = line.split(',')
        if len(parts) < 7:
            return False
        msg_type = parts[0]
        return msg_type.endswith('GGA') and msg_type.startswith('$G')

    def _start_mobile_data_writer(self):
        """启动移动站 GGA 数据异步写库线程"""
        if self.mobile_data_writer_running:
            return
        self.mobile_data_writer_running = True
        self.mobile_data_writer_thread = threading.Thread(target=self._mobile_data_writer_loop, daemon=True)
        self.mobile_data_writer_thread.start()
        logger.log_system_event('移动站 GGA 数据写库线程已启动')

    def _stop_mobile_data_writer(self):
        """停止移动站 GGA 数据写库线程，并 flush 队列中残留数据"""
        if not self.mobile_data_writer_running:
            return
        self.mobile_data_writer_running = False
        if self.mobile_data_writer_thread and self.mobile_data_writer_thread.is_alive():
            self.mobile_data_writer_thread.join(timeout=5)

        # flush 残留（最多 5 次批量），避免最后几条数据丢失
        for _ in range(5):
            rows = self._drain_queue(self.MOBILE_DATA_FLUSH_BATCH)
            if not rows:
                break
            try:
                database.add_mobile_data_batch(rows)
            except Exception:
                pass

    def _drain_queue(self, max_items):
        """从队列中取出最多 max_items 条数据"""
        rows = []
        try:
            while len(rows) < max_items:
                rows.append(self.mobile_data_queue.get_nowait())
        except queue.Empty:
            pass
        return rows

    def _mobile_data_writer_loop(self):
        """移动站 GGA 数据写库工作循环

        触发批量写入的条件：
        1. 队列中累积条数 >= FLUSH_BATCH
        2. 距上次写入时间 >= FLUSH_INTERVAL 秒
        """
        last_flush_time = time.time()
        while self.mobile_data_writer_running:
            try:
                # 1) 立即取满一个 batch
                rows = self._drain_queue(self.MOBILE_DATA_FLUSH_BATCH)

                # 2) 如果批量未满，则用短超时等待凑够或超时
                if len(rows) < self.MOBILE_DATA_FLUSH_BATCH:
                    wait_seconds = self.MOBILE_DATA_FLUSH_INTERVAL - (time.time() - last_flush_time)
                    if wait_seconds > 0:
                        try:
                            extra = self.mobile_data_queue.get(timeout=wait_seconds)
                            rows.append(extra)
                        except queue.Empty:
                            pass

                if rows:
                    try:
                        written = database.add_mobile_data_batch(rows)
                        if written == 0 and rows:
                            # 整批失败：把数据放回队首，下次重试（避免静默丢失）
                            for row in rows:
                                try:
                                    self.mobile_data_queue.put_nowait(row)
                                except queue.Full:
                                    # 还是满就丢弃最旧的
                                    try:
                                        self.mobile_data_queue.get_nowait()
                                        self.mobile_data_queue.put_nowait(row)
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                            time.sleep(0.5)
                    except Exception as e:
                        logger.log_error(f'批量写入移动站数据失败: {e}', exc_info=True)
                    last_flush_time = time.time()
                else:
                    # 队列空时小憩，避免 busy loop
                    time.sleep(0.05)
            except Exception as e:
                logger.log_error(f'移动站数据写库线程异常: {e}', exc_info=True)
                time.sleep(0.5)

    def _enable_keepalive(self, client_socket):
        """TCP Keep-Alive"""
        try:
            if not config.TCP_KEEPALIVE['enabled']:
                return
                
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            
            
            try:
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, config.TCP_KEEPALIVE['idle'])
                if hasattr(socket, 'TCP_KEEPINTVL'):
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, config.TCP_KEEPALIVE['interval'])
                if hasattr(socket, 'TCP_KEEPCNT'):
                    client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, config.TCP_KEEPALIVE['count'])
                # 移除频繁的debug日志
                # logger.log_debug(f"TCP Keep-Alive已启用: idle={config.TCP_KEEPALIVE['idle']}s", 'ntrip')
            except OSError:
                # 移除频繁的debug日志
                # logger.log_debug("TCP Keep-Alive已启用（使用系统默认参数）", 'ntrip')
                pass
            
            
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, config.BUFFER_SIZE)
            client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, config.BUFFER_SIZE)
            
        except Exception as e:
            logger.log_warning(f"设置TCP Keep-Alive失败: {e}", 'ntrip')
    
    def remove_client(self, client_info, reason="客户端断开"):
        """移除客户端连接"""
        try:
            self._close_client(client_info)

            with self.client_lock:

                mount = client_info['mount']
                if mount in self.clients and client_info in self.clients[mount]:
                    self.clients[mount].remove(client_info)

                    if not self.clients[mount]:
                        del self.clients[mount]

                self.stats['active_clients'] = sum(len(clients) for clients in self.clients.values())
                self.stats['disconnected_clients'] += 1

            connection.remove_user_connection(
                client_info['user'],
                mount_name=client_info['mount'],
                reason=reason,
            )
            
            logger.log_client_disconnect(
                client_info['user'], 
                client_info['mount'], 
                client_info['addr'][0]
            )
            
        except Exception as e:
            logger.log_error(f"移除客户端失败: {e}", exc_info=True)
    
    def _close_client(self, client_info):
        """关闭客户端连接"""
        try:
            socket_obj = client_info['socket']
            # 先从GGA selector注销，避免已关闭socket被select返回
            self._unregister_gga_socket(socket_obj)
            socket_obj.close()
        except Exception as e:
            logger.log_debug(f"关闭客户端连接失败: {e}", 'ntrip')
    
    def upload_data(self, mount, data_chunk):
        """上传数据到指定挂载点"""
        timestamp = time.time()
        
        if mount not in self.mount_buffers:
            self.create_mount_buffer(mount)
        
        with self.buffer_lock:
            self.mount_buffers[mount].append(data_chunk, timestamp)
        
        self._send_to_subscribers(mount, data_chunk)
        
        try:
            connection.update_mount_data_stats(mount, len(data_chunk))
        except Exception as e:
            logger.log_error(f"更新挂载点 {mount} 数据统计时发生错误: {e}")
    
    def create_mount_buffer(self, mount):
        with self.buffer_lock:
            if mount not in self.mount_buffers:
                self.mount_buffers[mount] = RingBuffer(self.buffer_maxlen)
                logger.log_mount_operation('buffer_created', mount)
                return True
            return False
    
    def remove_mount_buffer(self, mount):
        with self.buffer_lock:
            if mount in self.mount_buffers:
                del self.mount_buffers[mount]
                logger.log_mount_operation('buffer_removed', mount)
                return True
            return False
    
    def _broadcast_loop(self):
        """广播循环"""
        logger.log_system_event('数据广播循环开始运行')
        
        while self.running:
            try:
                self._broadcast_data()
                time.sleep(self.broadcast_interval)
            except Exception as e:
                logger.log_error(f"广播循环异常: {e}", exc_info=True)
                time.sleep(1)
    
    def _broadcast_data(self):
        """广播数据到所有客户端"""
        with self.buffer_lock:
            mount_items = list(self.mount_buffers.items())
        
        for mount_name, buffer in mount_items:
            with self.client_lock:
                if mount_name in self.clients:
                    clients = self.clients[mount_name][:]
                    self._send_data_to_clients(clients, buffer, mount_name)
    
    def _send_data_to_clients(self, clients, buffer, mount_name):
        """发送数据到客户端列表"""
        disconnected_clients = []
        
        for client_info in clients:
            try:
                self._send_to_client(client_info, buffer)
            except Exception as e:
                logger.log_warning(f"发送数据到客户端失败 ({client_info['addr']}): {e}", 'ntrip')
                disconnected_clients.append(client_info)
        
        # 清理断开的连接
        for client_info in disconnected_clients:
            self.remove_client(client_info)
    
    def _send_to_client(self, client_info, buffer):
        """发送数据到单个客户端"""
        try:
            
            last_sent_timestamp = client_info['last_sent_timestamp']
            new_data = buffer.get_since(last_sent_timestamp)
            
            if new_data:
                
                bytes_sent = self._send_data_simple(client_info, new_data)
                
                if bytes_sent > 0:
                   
                    current_time = time.time()
                    client_info['last_seen'] = current_time
                    client_info['last_sent_timestamp'] = new_data[-1][0]
                    client_info['bytes_sent'] += bytes_sent
                    client_info['messages_sent'] += len(new_data)
                    
                    self.stats['total_bytes_sent'] += bytes_sent
                    self.stats['total_messages_sent'] += len(new_data)
                    
                    if client_info.get('connection_id'):
                        # 静默更新用户活动，不产生日志
                        connection.update_user_activity(
                            client_info['user'], 
                            client_info['connection_id'], 
                            bytes_sent
                        )
        
        except Exception as e:
            # 只在非网络错误时记录警告日志
            if "Connection" not in str(e) and "Broken pipe" not in str(e):
                logger.log_warning(f"发送数据到客户端失败 ({client_info['addr']}): {e}", 'ntrip')
            raise
    
    def _send_data_simple(self, client_info, data_list):
        """简单的数据发送方法"""
        try:
            socket_obj = client_info['socket']
            protocol_version = client_info['protocol_version']
            total_bytes_sent = 0
            
            for timestamp, data in data_list:
                if protocol_version == 'ntrip2_0':
                    # NTRIP 2.0 使用分块编码
                    chunk_size = hex(len(data))[2:].upper().encode('ascii')
                    chunk_data = chunk_size + b'\r\n' + data + b'\r\n'
                    socket_obj.sendall(chunk_data)
                    total_bytes_sent += len(chunk_data)
                else:
                    # NTRIP 1.0 直接发送
                    socket_obj.sendall(data)
                    total_bytes_sent += len(data)
            
            return total_bytes_sent
            
        except Exception as e:
            client_info['send_errors'] += 1
            self.stats['failed_sends'] += 1
            raise
    
    def get_stats(self):
        """获取转发器统计信息"""
        with self.buffer_lock, self.client_lock:
            buffer_stats = {}
            for mount, buffer in self.mount_buffers.items():
                buffer_stats[mount] = buffer.get_stats()
            
            return {
                'forwarder': self.stats.copy(),
                'buffers': buffer_stats,
                'clients_by_mount': {mount: len(clients) for mount, clients in self.clients.items()}
            }
    
    def get_client_info(self, mount=None):
        """获取客户端信息"""
        with self.client_lock:
            if mount:
                return self.clients.get(mount, [])
            else:
                return dict(self.clients)
    

    
    def force_disconnect_user(self, username):
        """强制断开指定用户的所有连接"""
        disconnected_count = 0
        clients_to_remove = []
        
        with self.client_lock:
            for mount_name, clients in self.clients.items():
                for client_info in clients[:]:
                    if client_info['user'] == username:
                        clients_to_remove.append(client_info)
        
        for client_info in clients_to_remove:
            try:
                self.remove_client(client_info, reason="强制断开")
                disconnected_count += 1
                logger.log_info(f"强制断开用户 {username} 的连接: {client_info['mount']}")
            except Exception as e:
                logger.log_error(f"强制断开用户 {username} 连接失败: {e}")
        
        logger.log_info(f"强制断开用户 {username} 完成，共断开 {disconnected_count} 个连接")
        return disconnected_count > 0
    
    def force_disconnect_mount(self, mount_name):
        """强制断开指定挂载点的所有连接"""
        disconnected_count = 0
        
        with self.client_lock:
            if mount_name in self.clients:
                clients_to_remove = self.clients[mount_name][:]
                
                for client_info in clients_to_remove:
                    try:
                        self.remove_client(client_info)
                        disconnected_count += 1
                        logger.log_info(f"强制断开挂载点 {mount_name} 的用户连接: {client_info['user']}")
                    except Exception as e:
                        logger.log_error(f"强制断开挂载点 {mount_name} 用户连接失败: {e}")
        
        try:
            self.remove_mount_buffer(mount_name)
            logger.log_info(f"移除挂载点 {mount_name} 的数据缓冲区")
        except Exception as e:
            logger.log_error(f"移除挂载点 {mount_name} 缓冲区失败: {e}")
        logger.log_info(f"强制断开挂载点 {mount_name} 完成，共断开 {disconnected_count} 个用户连接")
        return True
    
    def register_subscriber(self, mount_name, socket_write_end):
        """注册数据订阅者（用于RTCM解析等）"""
        with self.subscriber_lock:
            if mount_name not in self.subscribers:
                self.subscribers[mount_name] = []
            self.subscribers[mount_name].append(socket_write_end)
            logger.log_debug(f"添加解析线程订阅挂载点 {mount_name}", 'ntrip')
            logger.log_info(f"[DEBUG] 注册解析线程订阅 [挂载点: {mount_name}, 订阅者数: {len(self.subscribers[mount_name])}]")
    
    def unregister_subscriber(self, mount_name, socket_write_end):
        """注销数据订阅者"""
        with self.subscriber_lock:
            if mount_name in self.subscribers:
                try:
                    self.subscribers[mount_name].remove(socket_write_end)
                    if not self.subscribers[mount_name]:
                        del self.subscribers[mount_name]
                    logger.log_debug(f"关闭解析线程订阅从挂载点 {mount_name}", 'ntrip')
                except ValueError:
                    pass 
    
    def _send_to_subscribers(self, mount_name: str, data_chunk: bytes):
        """向订阅者发送数据"""
        # logger.log_debug(f"[FORWARDER] 正在向 {mount_name} 发送数据，订阅者数量: {len(self.subscribers.get(mount_name, []))}")
        with self.subscriber_lock:
            if mount_name in self.subscribers:
                subscribers_to_remove = []
                subscriber_count = len(self.subscribers[mount_name])
                # logger.log_debug(f"[DEBUG] 向挂载点 {mount_name} 的 {subscriber_count} 个订阅者发送数据 ({len(data_chunk)} 字节)", 'ntrip')
                
                if subscriber_count > 0:
                     # logger.log_info(f"[DEBUG] 发送RTCM数据 [挂载点: {mount_name}, 订阅者: {subscriber_count}, 数据长度: {len(data_chunk)}]", 'ntrip')
                     pass
                
                for i, subscriber in enumerate(self.subscribers[mount_name]):
                    try:
                        # 检查订阅者类型，socket对象使用send方法，文件对象使用write方法
                        if hasattr(subscriber, 'send'):
                            # socket对象
                            subscriber.send(data_chunk)
                        elif hasattr(subscriber, 'write'):
                            # 文件对象
                            subscriber.write(data_chunk)
                            subscriber.flush()
                        else:
                            raise AttributeError(f"订阅者对象不支持数据发送: {type(subscriber)}")
                        
                        # if i == 0:  # 只记录第一个订阅者的成功发送
                        #     logger.log_debug(f"[DEBUG] 成功发送到订阅者 #{i+1} [挂载点: {mount_name}]", 'ntrip')
                    except Exception as e:
                        logger.log_error(f"向解析线程订阅者 #{i+1} 发送数据失败 [挂载点: {mount_name}]: {e}", 'ntrip')
                        subscribers_to_remove.append(subscriber)
                
                # 移除失效的订阅者
                for subscriber in subscribers_to_remove:
                    try:
                        self.subscribers[mount_name].remove(subscriber)
                        logger.log_warning(f"[DEBUG] 移除解析线程失效订阅者 [挂载点: {mount_name}]", 'ntrip')
                    except ValueError:
                        pass
            
                if not self.subscribers[mount_name]:
                    del self.subscribers[mount_name]


forwarder = SimpleDataForwarder()

# 全局管理函数 扩容管理端
def initialize():
    """初始化数据转发器"""
    logger.log_system_event('数据转发器已初始化')
    return forwarder

def get_forwarder():
    """获取全局数据转发器实例"""
    return forwarder

def start_forwarder():
    """启动数据转发器"""
    forwarder.start()

def stop_forwarder():
    """停止数据转发器"""
    forwarder.stop()


def add_client(client_socket, user, mount, agent, addr, protocol_version, connection_id=None):
    """同步添加客户端（兼容原接口）"""
    try:
        return forwarder.add_client(client_socket, user, mount, agent, addr, protocol_version, connection_id)
    except Exception as e:
        logger.log_error(f"添加客户端超时: {e}", 'ntrip')
        raise

def remove_client(client_info):
    """移除客户端"""
    return forwarder.remove_client(client_info)

def upload_data(mount, data_chunk):
    """上传数据"""
    return forwarder.upload_data(mount, data_chunk)

def create_mount_buffer(mount):
    """创建挂载点缓冲区"""
    return forwarder.create_mount_buffer(mount)

def remove_mount_buffer(mount):
    """移除挂载点缓冲区"""
    return forwarder.remove_mount_buffer(mount)

def get_stats():
    """获取统计信息"""
    return forwarder.get_stats()

def get_client_info(mount=None):
    """获取客户端信息"""
    return forwarder.get_client_info(mount)

def force_disconnect_user(username):
    """强制断开指定用户的所有连接"""
    return forwarder.force_disconnect_user(username)

def force_disconnect_mount(mount_name):
    """强制断开挂载点"""
    return forwarder.force_disconnect_mount(mount_name)

def register_subscriber(mount_name, socket_write_end):
    """注册数据订阅者"""
    return forwarder.register_subscriber(mount_name, socket_write_end)

def unregister_subscriber(mount_name, socket_write_end):
    """注销数据订阅者"""
    return forwarder.unregister_subscriber(mount_name, socket_write_end)