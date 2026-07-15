#!/usr/bin/env python3

import sqlite3
import hashlib
import secrets
import logging
from threading import Lock
from . import config
from . import logger
from .logger import log_debug, log_info, log_warning, log_error, log_critical, log_database_operation, log_authentication

db_lock = Lock()


def hash_password(password, salt=None):
    """使用PBKDF2和SHA256哈希密码"""
    if salt is None:
        salt = secrets.token_hex(16)  
    
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 10000)
    return f"{salt}${key.hex()}"

def verify_password(stored_password, provided_password):
    """验证密码是否匹配"""
    
    if '$' not in stored_password:
       
        return stored_password == provided_password
        
    salt, hash_value = stored_password.split('$', 1)
    
    key = hashlib.pbkdf2_hmac('sha256', provided_password.encode(), salt.encode(), 10000)
    
    return key.hex() == hash_value

def init_db():
    """初始化SQLite数据库表结构"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()

        # 管理员表
        c.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
        ''')
        
        # 用户表（NTRIP客户端用户）
        c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
        ''')
        
        # 挂载点表
        c.execute('''
        CREATE TABLE IF NOT EXISTS mounts (
            id INTEGER PRIMARY KEY,
            mount TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            user_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
                ON DELETE SET NULL
                ON UPDATE CASCADE
        )
        ''')
        
        # 消息机器人表
        c.execute('''
        CREATE TABLE IF NOT EXISTS notification_bots (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            platform TEXT NOT NULL CHECK(platform IN ('dingtalk', 'wecom')),
            webhook_url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 消息机器人订阅事件表
        # 事件类型：base_station_online（基站上线）、base_station_offline（基站下线）、
        #           mount_online（挂载点/用户连接上线）、mount_offline（挂载点/用户连接下线）
        c.execute('''
        CREATE TABLE IF NOT EXISTS notification_events (
            id INTEGER PRIMARY KEY,
            bot_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            FOREIGN KEY (bot_id) REFERENCES notification_bots(id)
                ON DELETE CASCADE
        )
        ''')
        
        # 连接事件日志表（基站/挂载点/用户连接上下线记录）
        c.execute('''
        CREATE TABLE IF NOT EXISTS connection_events (
            id INTEGER PRIMARY KEY,
            event_type TEXT NOT NULL,
            mount_name TEXT,
            username TEXT,
            ip_address TEXT,
            event_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            duration REAL,
            reason TEXT,
            details TEXT
        )
        ''')
        
        # 为常用查询字段创建索引
        c.execute('CREATE INDEX IF NOT EXISTS idx_connection_events_event_type ON connection_events(event_type)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_connection_events_mount_name ON connection_events(mount_name)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_connection_events_username ON connection_events(username)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_connection_events_event_time ON connection_events(event_time)')
        
        c.execute("SELECT * FROM admins")
        if not c.fetchone():
            # 使用哈希密码存储默认管理员密码
            admin_username = config.DEFAULT_ADMIN['username']
            admin_password = config.DEFAULT_ADMIN['password']
            hashed_password = hash_password(admin_password)
            c.execute("INSERT INTO admins (username, password) VALUES (?, ?)", (admin_username, hashed_password))
            print(f"已创建默认管理员: {admin_username}/{admin_password}（请首次登录后修改）")
        
        conn.commit()
        conn.close()
        log_info('数据库初始化完成')

def verify_mount_and_user(mount, username=None, password=None, mount_password=None, protocol_version="1.0"):
    """验证挂载点和用户信息是否合法
    
    Args:
        mount: 挂载点名称
        username: 用户名（可选）
        password: 用户密码（可选）
        mount_password: 挂载点密码（可选）
    """
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        
        try:
            # 检查挂载点是否存在并获取相关信息
            c.execute("SELECT id, password, user_id FROM mounts WHERE mount = ?", (mount,))
            mount_result = c.fetchone()
            
            if not mount_result:
                log_authentication(username or 'unknown', mount, False, 'database', '挂载点不存在')
                return False, "挂载点不存在"
            
            mount_id, stored_mount_password, bound_user_id = mount_result
            
            # 根据协议版本进行不同的验证逻辑
            if protocol_version == "2.0":
                
                if not username or not password:
                    log_authentication(username or 'unknown', mount, False, 'database', 'NTRIP 2.0需要用户名和密码')
                    return False, "NTRIP 2.0协议需要提供用户名和密码"
                
                # 验证用户是否存在
                c.execute("SELECT id, password FROM users WHERE username = ?", (username,))
                user_result = c.fetchone()
                if not user_result:
                    log_authentication(username, mount, False, 'database', '用户不存在')
                    return False, "用户不存在"
                
                user_id, stored_user_password = user_result
                
                # 验证用户密码
                if not verify_password(stored_user_password, password):
                    log_authentication(username, mount, False, 'database', '用户密码错误')
                    return False, "用户密码错误"
                
                # 验证挂载点是否绑定到该用户
                if bound_user_id is not None and bound_user_id != user_id:
                    log_authentication(username, mount, False, 'database', '用户无权限访问该挂载点')
                    return False, "用户无权限访问该挂载点"
                
                # NTRIP 2.0 不验证挂载点密码，只验证用户名和密码以及挂着的所属权限
                log_authentication(username, mount, True, 'database', 'NTRIP 2.0认证成功')
                return True, "NTRIP 2.0认证成功"
            
            else:
                # NTRIP 1.0 及以下版本验证逻辑
                if not mount_password:
                    log_authentication(username or 'unknown', mount, False, 'database', 'NTRIP 1.0需要挂载点密码')
                    return False, "NTRIP 1.0协议需要提供挂载点密码"
                
                # 验证挂载点密码
                if stored_mount_password != mount_password:
                    log_authentication(username or 'unknown', mount, False, 'database', '挂载点密码错误')
                    return False, "挂载点密码错误"
                
                # NTRIP 1.0 只验证挂载点和挂载点密码，不验证用户
                log_authentication(username or 'unknown', mount, True, 'database', 'NTRIP 1.0认证成功')
                return True, "NTRIP 1.0认证成功"
            
        except Exception as e:
            log_error(f"用户认证异常: {e}", exc_info=True)
            return False, f"认证异常: {e}"
        finally:
            conn.close()



def add_user(username, password):
    """添加新用户到数据库"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            # 检查用户是否已存在
            c.execute("SELECT * FROM users WHERE username = ?", (username,))
            if c.fetchone():
                return False, "用户名已存在"
            
            # 哈希密码并添加用户
            hashed_password = hash_password(password)
            c.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_password))
            conn.commit()
            log_database_operation('add_user', 'users', True, f'用户: {username}')
            return True, "用户添加成功"
        except Exception as e:
            log_database_operation('add_user', 'users', False, str(e))
            return False, f"添加用户失败: {e}"
        finally:
            conn.close()

def update_user(user_id, username, password):
    """更新用户信息"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            # 检查用户名是否与其他用户冲突
            c.execute("SELECT * FROM users WHERE username = ? AND id != ?", (username, user_id))
            if c.fetchone():
                return False, "用户名已存在"
            
            c.execute("SELECT password FROM users WHERE id = ?", (user_id,))
            old_password = c.fetchone()[0]
            
            if '$' in old_password and verify_password(old_password, password):
                new_password = old_password
            else:
                new_password = hash_password(password)
            
            c.execute("UPDATE users SET username = ?, password = ? WHERE id = ?", (username, new_password, user_id))
            conn.commit()
            log_database_operation('update_user', 'users', True, f'用户: {username}')
            return True, "用户更新成功"
        except Exception as e:
            log_database_operation('update_user', 'users', False, str(e))
            return False, f"更新用户失败: {e}"
        finally:
            conn.close()

# ==================== 消息机器人配置管理 ====================

def get_all_notification_bots():
    """获取所有消息机器人配置，包含订阅事件类型"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute('''
                SELECT id, name, platform, webhook_url, enabled, created_at
                FROM notification_bots
                ORDER BY id
            ''')
            bots = c.fetchall()
            
            c.execute('SELECT bot_id, event_type FROM notification_events')
            events = c.fetchall()
            
            bot_events = {}
            for bot_id, event_type in events:
                bot_events.setdefault(bot_id, []).append(event_type)
            
            result = []
            for bot in bots:
                result.append({
                    'id': bot[0],
                    'name': bot[1],
                    'platform': bot[2],
                    'webhook_url': bot[3],
                    'enabled': bool(bot[4]),
                    'created_at': bot[5],
                    'events': bot_events.get(bot[0], [])
                })
            return result
        finally:
            conn.close()


def get_notification_bots():
    """获取所有启用的消息机器人配置，包含订阅事件类型"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute('''
                SELECT id, name, platform, webhook_url, enabled
                FROM notification_bots
                WHERE enabled = 1
                ORDER BY id
            ''')
            bots = c.fetchall()
            
            c.execute('SELECT bot_id, event_type FROM notification_events')
            events = c.fetchall()
            
            bot_events = {}
            for bot_id, event_type in events:
                bot_events.setdefault(bot_id, []).append(event_type)
            
            result = []
            for bot in bots:
                result.append({
                    'id': bot[0],
                    'name': bot[1],
                    'platform': bot[2],
                    'webhook_url': bot[3],
                    'enabled': bool(bot[4]),
                    'events': bot_events.get(bot[0], [])
                })
            return result
        finally:
            conn.close()


def add_notification_bot(name, platform, webhook_url, enabled, events):
    """添加消息机器人配置
    
    Args:
        name: 机器人名称
        platform: 平台类型 'dingtalk' 或 'wecom'
        webhook_url: Webhook 地址
        enabled: 是否启用
        events: 订阅事件类型列表，如 ['mount_online', 'mount_offline']
    """
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            if platform not in ('dingtalk', 'wecom'):
                return False, "不支持的机器人平台"
            
            allowed_events = {
                'base_station_online', 'base_station_offline',
                'mount_online', 'mount_offline'
            }
            valid_events = []
            for event in events:
                if event in allowed_events:
                    valid_events.append(event)
                else:
                    return False, f"无效的事件类型: {event}"
            
            c.execute('''
                INSERT INTO notification_bots (name, platform, webhook_url, enabled)
                VALUES (?, ?, ?, ?)
            ''', (name, platform, webhook_url, 1 if enabled else 0))
            bot_id = c.lastrowid
            
            for event in valid_events:
                c.execute('''
                    INSERT INTO notification_events (bot_id, event_type)
                    VALUES (?, ?)
                ''', (bot_id, event))
            
            conn.commit()
            log_database_operation('add_notification_bot', 'notification_bots', True, f'机器人: {name}, 平台: {platform}')
            return True, "机器人添加成功"
        except Exception as e:
            log_database_operation('add_notification_bot', 'notification_bots', False, str(e))
            return False, f"添加机器人失败: {e}"
        finally:
            conn.close()


def update_notification_bot(bot_id, name, platform, webhook_url, enabled, events):
    """更新消息机器人配置"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute('SELECT id FROM notification_bots WHERE id = ?', (bot_id,))
            if not c.fetchone():
                return False, "机器人不存在"
            
            if platform not in ('dingtalk', 'wecom'):
                return False, "不支持的机器人平台"
            
            allowed_events = {
                'base_station_online', 'base_station_offline',
                'mount_online', 'mount_offline'
            }
            valid_events = []
            for event in events:
                if event in allowed_events:
                    valid_events.append(event)
                else:
                    return False, f"无效的事件类型: {event}"
            
            c.execute('''
                UPDATE notification_bots
                SET name = ?, platform = ?, webhook_url = ?, enabled = ?
                WHERE id = ?
            ''', (name, platform, webhook_url, 1 if enabled else 0, bot_id))
            
            c.execute('DELETE FROM notification_events WHERE bot_id = ?', (bot_id,))
            
            for event in valid_events:
                c.execute('''
                    INSERT INTO notification_events (bot_id, event_type)
                    VALUES (?, ?)
                ''', (bot_id, event))
            
            conn.commit()
            log_database_operation('update_notification_bot', 'notification_bots', True, f'机器人ID: {bot_id}')
            return True, "机器人更新成功"
        except Exception as e:
            log_database_operation('update_notification_bot', 'notification_bots', False, str(e))
            return False, f"更新机器人失败: {e}"
        finally:
            conn.close()


def delete_notification_bot(bot_id):
    """删除消息机器人配置"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute('SELECT name FROM notification_bots WHERE id = ?', (bot_id,))
            result = c.fetchone()
            if not result:
                return False, "机器人不存在"
            
            name = result[0]
            c.execute('DELETE FROM notification_bots WHERE id = ?', (bot_id,))
            conn.commit()
            log_database_operation('delete_notification_bot', 'notification_bots', True, f'机器人: {name}')
            return True, "机器人删除成功"
        except Exception as e:
            log_database_operation('delete_notification_bot', 'notification_bots', False, str(e))
            return False, f"删除机器人失败: {e}"
        finally:
            conn.close()


def delete_user(user_id):
    """删除用户"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            
            c.execute("SELECT username FROM users WHERE id = ?", (user_id,))
            result = c.fetchone()
            if not result:
                return False, "用户不存在"
            
            username = result[0]
            
            # 先清除所有绑定到该用户的挂载点的user_id
            c.execute("UPDATE mounts SET user_id = NULL WHERE user_id = ?", (user_id,))
            affected_mounts = c.rowcount
            
            # 删除用户
            c.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            
            log_message = f'用户: {username}'
            if affected_mounts > 0:
                log_message += f', 同时清除了 {affected_mounts} 个挂载点的用户绑定'
            
            log_database_operation('delete_user', 'users', True, log_message)
            return True, username
        except Exception as e:
            log_database_operation('delete_user', 'users', False, str(e))
            return False, f"删除用户失败: {e}"
        finally:
            conn.close()

def get_all_users():
    """获取所有用户列表"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute("SELECT id, username, password FROM users")
            return c.fetchall()
        finally:
            conn.close()

def update_user_password(username, new_password):
    """更新用户密码"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            
            c.execute("SELECT id FROM users WHERE username = ?", (username,))
            result = c.fetchone()
            if not result:
                return False, "用户不存在"
            
            
            hashed_password = hash_password(new_password)
            
            c.execute("UPDATE users SET password = ? WHERE username = ?", (hashed_password, username))
            conn.commit()
            log_info(f"用户 {username} 密码更新成功")
            return True, "密码更新成功"
        except Exception as e:
            log_error(f"更新用户密码失败: {e}")
            return False, f"更新密码失败: {e}"
        finally:
            conn.close()

def add_mount(mount, password, user_id=None):
    """添加新挂载点"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
           
            c.execute("SELECT * FROM mounts WHERE mount = ?", (mount,))
            if c.fetchone():
                return False, "挂载点名称已存在"
            
            # 如果指定了用户ID，验证用户是否存在
            if user_id is not None:
                c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
                if not c.fetchone():
                    return False, "指定的用户不存在"
            
            c.execute("INSERT INTO mounts (mount, password, user_id) VALUES (?, ?, ?)", (mount, password, user_id))
            conn.commit()
            log_database_operation('add_mount', 'mounts', True, f'挂载点: {mount}, 用户ID: {user_id}')
            return True, "挂载点添加成功"
        except Exception as e:
            log_database_operation('add_mount', 'mounts', False, str(e))
            return False, f"添加挂载点失败: {e}"
        finally:
            conn.close()

def update_mount(mount_id, mount=None, password=None, user_id=None):
    """更新挂载点信息"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            
            c.execute("SELECT mount, password, user_id FROM mounts WHERE id = ?", (mount_id,))
            result = c.fetchone()
            if not result:
                return False, "挂载点不存在"
            
            old_mount, old_password, old_user_id = result
            
            
            new_mount = mount if mount is not None else old_mount
            new_password = password if password is not None else old_password
            new_user_id = user_id if user_id != 'keep_current' else old_user_id
            
            # 检查挂载点名称是否与其他挂载点冲突
            if mount is not None and mount != old_mount:
                c.execute("SELECT * FROM mounts WHERE mount = ? AND id != ?", (mount, mount_id))
                if c.fetchone():
                    return False, "挂载点名称已存在"
            # 如果指定了用户ID，验证用户是否存在
            if new_user_id is not None:
                c.execute("SELECT id FROM users WHERE id = ?", (new_user_id,))
                if not c.fetchone():
                    return False, "指定的用户不存在"
            
            c.execute("UPDATE mounts SET mount = ?, password = ?, user_id = ? WHERE id = ?", (new_mount, new_password, new_user_id, mount_id))
            conn.commit()
            log_database_operation('update_mount', 'mounts', True, f'挂载点: {old_mount} -> {new_mount}')
            return True, old_mount
        except Exception as e:
            log_database_operation('update_mount', 'mounts', False, str(e))
            return False, f"更新挂载点失败: {e}"
        finally:
            conn.close()

def delete_mount(mount_id):
    """删除挂载点"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            
            c.execute("SELECT mount FROM mounts WHERE id = ?", (mount_id,))
            result = c.fetchone()
            if not result:
                return False, "挂载点不存在"
            
            mount = result[0]
            c.execute("DELETE FROM mounts WHERE id = ?", (mount_id,))
            conn.commit()
            log_database_operation('delete_mount', 'mounts', True, f'挂载点: {mount}')
            return True, mount
        except Exception as e:
            logger.log_database_operation('delete_mount', 'mounts', False, str(e))
            return False, f"删除挂载点失败: {e}"
        finally:
            conn.close()

def get_all_mounts():
    """获取所有挂载点列表"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute("PRAGMA table_info(mounts)")
            columns = [column[1] for column in c.fetchall()]
            
            if 'lat' in columns and 'lon' in columns:
                c.execute("""SELECT m.id, m.mount, m.password, m.user_id, u.username, m.lat, m.lon
                             FROM mounts m 
                             LEFT JOIN users u ON m.user_id = u.id""")
            else:
                c.execute("""SELECT m.id, m.mount, m.password, m.user_id, u.username, NULL as lat, NULL as lon
                             FROM mounts m 
                             LEFT JOIN users u ON m.user_id = u.id""")
            return c.fetchall()
        finally:
            conn.close()


def verify_admin(username, password):
    """验证管理员账号密码"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute("SELECT password FROM admins WHERE username = ?", (username,))
            result = c.fetchone()
            if result and verify_password(result[0], password):
                return True
            return False
        finally:
            conn.close()

def update_admin_password(username, new_password):
    """更新管理员密码"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            hashed_password = hash_password(new_password)
            c.execute("UPDATE admins SET password = ? WHERE username = ?", (hashed_password, username))
            conn.commit()
            log_database_operation('update_admin_password', 'admins', True, f'管理员: {username}')
            return True
        except Exception as e:
            log_database_operation('update_admin_password', 'admins', False, str(e))
            return False
        finally:
            conn.close()


# ==================== 连接事件日志管理 ====================

def add_connection_event(event_type, mount_name=None, username=None, ip_address=None, duration=None, reason=None, details=None):
    """记录连接事件日志（基站/挂载点/用户连接上下线）
    
    Args:
        event_type: 事件类型，如 'base_station_online', 'base_station_offline',
                    'mount_online', 'mount_offline'
        mount_name: 挂载点名称
        username: 用户名
        ip_address: IP 地址
        duration: 连接时长（秒），下线事件时填写
        reason: 下线原因
        details: 额外详情（JSON 字符串等）
    """
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            c.execute('''
                INSERT INTO connection_events
                (event_type, mount_name, username, ip_address, duration, reason, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (event_type, mount_name, username, ip_address, duration, reason, details))
            conn.commit()
            return True, c.lastrowid
        except Exception as e:
            log_error(f'记录连接事件失败: {e}', exc_info=True)
            return False, str(e)
        finally:
            conn.close()


def get_connection_events_count(event_type=None, mount_name=None, username=None, start_time=None, end_time=None):
    """获取连接事件日志总数（用于分页）"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            query = 'SELECT COUNT(*) FROM connection_events WHERE 1=1'
            params = []
            if event_type:
                query += ' AND event_type = ?'
                params.append(event_type)
            if mount_name:
                query += ' AND mount_name = ?'
                params.append(mount_name)
            if username:
                query += ' AND username = ?'
                params.append(username)
            if start_time:
                query += ' AND event_time >= ?'
                params.append(start_time)
            if end_time:
                query += ' AND event_time <= ?'
                params.append(end_time)
            c.execute(query, params)
            return c.fetchone()[0]
        finally:
            conn.close()


def cleanup_old_connection_events(retention_days=365, max_records=100000):
    """清理连接事件日志
    
    策略：
    1. 删除超过保留天数的记录（按 event_time 计算）。
    2. 如果总条数仍超过 max_records，删除最旧的记录，直到总数 <= max_records。
    
    Args:
        retention_days: 保留天数，默认365天（1年）
        max_records: 最大保留记录数，默认100000条
    """
    if retention_days <= 0:
        log_warning('连接事件日志保留天数配置无效，跳过清理')
        return 0
    
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        total_deleted = 0
        try:
            # 1. 按时间删除超过保留期限的记录
            c.execute('''
                DELETE FROM connection_events
                WHERE event_time < datetime('now', ?)
            ''', (f'-{retention_days} days',))
            deleted_by_time = c.rowcount
            total_deleted += deleted_by_time
            
            # 2. 按数量限制删除最旧的记录（允许一周误差，因此优先保留时间更早一周的阈值）
            # 实际逻辑：保留最近 365 天的记录，同时保证总条数不超过 max_records
            c.execute('SELECT COUNT(*) FROM connection_events')
            current_count = c.fetchone()[0]
            if current_count > max_records:
                overflow = current_count - max_records
                c.execute('''
                    DELETE FROM connection_events
                    WHERE id IN (
                        SELECT id FROM connection_events
                        ORDER BY event_time ASC
                        LIMIT ?
                    )
                ''', (overflow,))
                deleted_by_count = c.rowcount
                total_deleted += deleted_by_count
            else:
                deleted_by_count = 0
            
            conn.commit()
            if total_deleted > 0:
                log_info(f'已清理 {total_deleted} 条连接事件日志（按时间 {deleted_by_time} 条，按数量 {deleted_by_count} 条），当前保留 {max_records} 条以内')
                log_database_operation('cleanup_old_connection_events', 'connection_events', True, f'删除 {total_deleted} 条记录')
            return total_deleted
        except Exception as e:
            log_error(f'清理连接事件日志失败: {e}', exc_info=True)
            log_database_operation('cleanup_old_connection_events', 'connection_events', False, str(e))
            return 0
        finally:
            conn.close()


def get_connection_events(limit=100, offset=0, event_type=None, mount_name=None, username=None, start_time=None, end_time=None):
    """获取连接事件日志列表
    
    Args:
        limit: 返回最大条数
        offset: 偏移量，用于分页
        event_type: 按事件类型筛选
        mount_name: 按挂载点名称筛选
        username: 按用户名筛选
        start_time: 开始时间（格式 'YYYY-MM-DD HH:MM:SS'）
        end_time: 结束时间（格式 'YYYY-MM-DD HH:MM:SS'）
    """
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            query = '''
                SELECT id, event_type, mount_name, username, ip_address, event_time, duration, reason, details
                FROM connection_events
                WHERE 1=1
            '''
            params = []
            if event_type:
                query += ' AND event_type = ?'
                params.append(event_type)
            if mount_name:
                query += ' AND mount_name = ?'
                params.append(mount_name)
            if username:
                query += ' AND username = ?'
                params.append(username)
            if start_time:
                query += ' AND event_time >= ?'
                params.append(start_time)
            if end_time:
                query += ' AND event_time <= ?'
                params.append(end_time)
            query += ' ORDER BY event_time DESC LIMIT ? OFFSET ?'
            params.append(limit)
            params.append(offset)
            
            c.execute(query, params)
            rows = c.fetchall()
            
            result = []
            event_labels = {
                'base_station_online': '基站上线',
                'base_station_offline': '基站下线',
                'mount_online': '挂载点上线',
                'mount_offline': '挂载点下线'
            }
            for row in rows:
                result.append({
                    'id': row[0],
                    'event_type': row[1],
                    'event_label': event_labels.get(row[1], row[1]),
                    'mount_name': row[2],
                    'username': row[3],
                    'ip_address': row[4],
                    'event_time': row[5],
                    'duration': row[6],
                    'reason': row[7],
                    'details': row[8]
                })
            return result
        finally:
            conn.close()


def get_connection_events_statistics(start_time=None, end_time=None):
    """获取连接事件统计信息"""
    with db_lock:
        conn = sqlite3.connect(config.DATABASE_PATH)
        c = conn.cursor()
        try:
            query = 'SELECT event_type, COUNT(*) FROM connection_events WHERE 1=1'
            params = []
            if start_time:
                query += ' AND event_time >= ?'
                params.append(start_time)
            if end_time:
                query += ' AND event_time <= ?'
                params.append(end_time)
            query += ' GROUP BY event_type'
            c.execute(query, params)
            return {row[0]: row[1] for row in c.fetchall()}
        finally:
            conn.close()


class DatabaseManager:
    """数据库管理器类，包装数据库操作函数"""
    
    def __init__(self):
        """初始化数据库管理器"""
        pass
    
    def init_database(self):
        """初始化数据库"""
        return init_db()
    
    def verify_mount_and_user(self, mount, username=None, password=None, mount_password=None, protocol_version="1.0"):
        """验证挂载点和用户"""
        return verify_mount_and_user(mount, username, password, mount_password, protocol_version)
    
    def add_user(self, username, password):
        """添加用户"""
        return add_user(username, password)
    
    def update_user_password(self, username, new_password):
        """更新用户密码"""
        return update_user_password(username, new_password)
    
    def delete_user(self, username):
        """删除用户"""
        users = get_all_users()
        user_id = None
        for user in users:
            if user[1] == username:  # user[1] 是 username
                user_id = user[0]    # user[0] 是 id
                break
        
        if user_id is None:
            return False, "用户不存在"
        
        return delete_user(user_id)
    
    def get_all_users(self):
        """获取所有用户"""
        return get_all_users()
    
    def get_user_password(self, username):
        """获取用户密码，用于Digest认证"""
        with sqlite3.connect(config.DATABASE_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT password FROM users WHERE username = ?", (username,))
            result = c.fetchone()
            return result[0] if result else None
    
    def check_mount_exists_in_db(self, mount):
        """检查挂载点是否在数据库中存在"""
        with sqlite3.connect(config.DATABASE_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM mounts WHERE mount = ?", (mount,))
            return c.fetchone() is not None
    
    def verify_download_user(self, mount, username, password):
        """验证下载用户，只验证用户名密码，不验证挂载点绑定关系"""
        with sqlite3.connect(config.DATABASE_PATH) as conn:
            c = conn.cursor()
            
            c.execute("SELECT id FROM mounts WHERE mount = ?", (mount,))
            mount_result = c.fetchone()
            if not mount_result:
                logger.log_authentication(username, mount, False, 'database', '挂载点不存在')
                return False, "挂载点不存在"
            
            c.execute("SELECT id, password FROM users WHERE username = ?", (username,))
            user_result = c.fetchone()
            if not user_result:
                logger.log_authentication(username, mount, False, 'database', '用户不存在')
                return False, "用户不存在"
            
            user_id, stored_password = user_result
            
            if not verify_password(stored_password, password):
                logger.log_authentication(username, mount, False, 'database', '用户密码错误')
                return False, "用户密码错误"
            
           
            logger.log_authentication(username, mount, True, 'database', '下载认证成功')
            return True, "下载认证成功"
    
    def add_mount(self, mount, password=None, user_id=None):
        """添加挂载点"""
        return add_mount(mount, password, user_id)
    
    def update_mount_password(self, mount, new_password):
        """更新挂载点密码"""
        with db_lock:
            conn = sqlite3.connect(config.DATABASE_PATH)
            c = conn.cursor()
            try:
                c.execute("UPDATE mounts SET password = ? WHERE mount = ?", (new_password, mount))
                if c.rowcount > 0:
                    conn.commit()
                    return True, "挂载点密码更新成功"
                else:
                    return False, "挂载点不存在"
            except Exception as e:
                return False, f"更新挂载点密码失败: {str(e)}"
            finally:
                conn.close()
    
    def update_user(self, user_id, username, password):
        """更新用户信息"""
        return update_user(user_id, username, password)
    
    def update_mount(self, mount_id, mount=None, password=None, user_id=None):
        """更新挂载点信息"""
        return update_mount(mount_id, mount, password, user_id)
    
    def delete_mount(self, mount):
        """删除挂载点"""
        mounts = self.get_all_mounts()
        mount_id = None
        for m in mounts:
            if m[1] == mount:  # m[1] 是挂载点名称
                mount_id = m[0]  # m[0] 是ID
                break
        
        if mount_id is None:
            return False, "挂载点不存在"
        
        return delete_mount(mount_id)
    
    def get_all_mounts(self):
        """获取所有挂载点"""
        return get_all_mounts()
       
    def verify_admin(self, username, password):
        """验证管理员"""
        return verify_admin(username, password)
    
    def update_admin_password(self, username, new_password):
        """更新管理员密码"""
        return update_admin_password(username, new_password)
    
    def get_all_notification_bots(self):
        """获取所有消息机器人配置"""
        return get_all_notification_bots()
    
    def get_notification_bots(self):
        """获取所有启用的消息机器人配置"""
        return get_notification_bots()
    
    def add_notification_bot(self, name, platform, webhook_url, enabled, events):
        """添加消息机器人配置"""
        return add_notification_bot(name, platform, webhook_url, enabled, events)
    
    def update_notification_bot(self, bot_id, name, platform, webhook_url, enabled, events):
        """更新消息机器人配置"""
        return update_notification_bot(bot_id, name, platform, webhook_url, enabled, events)
    
    def delete_notification_bot(self, bot_id):
        """删除消息机器人配置"""
        return delete_notification_bot(bot_id)
    
    def add_connection_event(self, event_type, mount_name=None, username=None, ip_address=None, duration=None, reason=None, details=None):
        """记录连接事件日志"""
        return add_connection_event(event_type, mount_name, username, ip_address, duration, reason, details)
    
    def get_connection_events(self, limit=100, offset=0, event_type=None, mount_name=None, username=None, start_time=None, end_time=None):
        """获取连接事件日志列表"""
        return get_connection_events(limit, offset, event_type, mount_name, username, start_time, end_time)
    
    def get_connection_events_statistics(self, start_time=None, end_time=None):
        """获取连接事件统计"""
        return get_connection_events_statistics(start_time, end_time)
    
    def get_connection_events_count(self, event_type=None, mount_name=None, username=None, start_time=None, end_time=None):
        """获取连接事件日志总数"""
        return get_connection_events_count(event_type, mount_name, username, start_time, end_time)
    
    def cleanup_old_connection_events(self, retention_days=365, max_records=100000):
        """清理连接事件日志"""
        return cleanup_old_connection_events(retention_days, max_records)
    
