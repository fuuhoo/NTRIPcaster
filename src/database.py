#!/usr/bin/env python3

import sqlite3
import hashlib
import secrets
import logging
import threading
from threading import Lock
from . import config
from . import logger
from .logger import log_debug, log_info, log_warning, log_error, log_critical, log_database_operation, log_authentication

db_lock = Lock()

# 全局SQLite连接：单连接复用 + WAL模式，避免每次操作都open/close连接的开销
_db_conn = None
_db_conn_lock = threading.Lock()


def _get_conn():
    """获取全局SQLite连接（单连接复用，开启WAL与busy_timeout）"""
    global _db_conn
    if _db_conn is None:
        with _db_conn_lock:
            if _db_conn is None:
                conn = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False)
                # 自动提交模式，避免异常残留未提交事务污染后续操作
                conn.isolation_level = None
                try:
                    conn.execute('PRAGMA journal_mode=WAL')
                    conn.execute('PRAGMA busy_timeout=10000')
                    conn.execute('PRAGMA foreign_keys=ON')
                except Exception:
                    pass
                _db_conn = conn
    return _db_conn


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
        conn = _get_conn()
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
            secret TEXT,
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
        c.execute('CREATE INDEX IF NOT EXISTS idx_connection_events_ip_address ON connection_events(ip_address)')

        # 移动站 GGA 数据表（移动站发送给 caster 的反向 NMEA 数据）
        c.execute('''
        CREATE TABLE IF NOT EXISTS mobile_station_data (
            id INTEGER PRIMARY KEY,
            event_time TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            username TEXT,
            mount_name TEXT,
            ip_address TEXT,
            nmea_type TEXT,
            raw_data TEXT NOT NULL,
            data_size INTEGER,
            gga_quality INTEGER
        )
        ''')
        # 主键 id 自增，单调递增，按 id 升序删除即等价于按时间删除（且更快）
        c.execute('CREATE INDEX IF NOT EXISTS idx_msd_event_time ON mobile_station_data(event_time)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_msd_username   ON mobile_station_data(username)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_msd_mount_name ON mobile_station_data(mount_name)')
        # 兼容旧 DB：检查 gga_quality 列是否存在，不存在则加
        c.execute("PRAGMA table_info(mobile_station_data)")
        cols = {row[1] for row in c.fetchall()}
        if 'gga_quality' not in cols:
            c.execute('ALTER TABLE mobile_station_data ADD COLUMN gga_quality INTEGER')
        c.execute('CREATE INDEX IF NOT EXISTS idx_msd_gga_quality ON mobile_station_data(gga_quality)')
        
        c.execute("SELECT * FROM admins")
        if not c.fetchone():
            # 使用哈希密码存储默认管理员密码
            admin_username = config.DEFAULT_ADMIN['username']
            admin_password = config.DEFAULT_ADMIN['password']
            hashed_password = hash_password(admin_password)
            c.execute("INSERT INTO admins (username, password) VALUES (?, ?)", (admin_username, hashed_password))
            print(f"已创建默认管理员: {admin_username}/{admin_password}（请首次登录后修改）")
        
        # 迁移：为旧数据库的notification_bots表添加secret字段
        c.execute("PRAGMA table_info(notification_bots)")
        columns = [col[1] for col in c.fetchall()]
        if 'secret' not in columns:
            c.execute('ALTER TABLE notification_bots ADD COLUMN secret TEXT')
            log_info('数据库迁移：为notification_bots表添加secret字段')
        
        conn.commit()
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
        conn = _get_conn()
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
            pass



def add_user(username, password):
    """添加新用户到数据库"""
    with db_lock:
        conn = _get_conn()
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
            pass

def update_user(user_id, username, password):
    """更新用户信息"""
    with db_lock:
        conn = _get_conn()
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
            pass

# ==================== 消息机器人配置管理 ====================

def get_all_notification_bots():
    """获取所有消息机器人配置，包含订阅事件类型"""
    with db_lock:
        conn = _get_conn()
        c = conn.cursor()
        try:
            c.execute('''
                SELECT id, name, platform, webhook_url, secret, enabled, created_at
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
                    'secret': bot[4],
                    'enabled': bool(bot[5]),
                    'created_at': bot[6],
                    'events': bot_events.get(bot[0], [])
                })
            return result
        finally:
            pass


def get_notification_bots():
    """获取所有启用的消息机器人配置，包含订阅事件类型"""
    with db_lock:
        conn = _get_conn()
        c = conn.cursor()
        try:
            c.execute('''
                SELECT id, name, platform, webhook_url, secret, enabled
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
                    'secret': bot[4],
                    'enabled': bool(bot[5]),
                    'events': bot_events.get(bot[0], [])
                })
            return result
        finally:
            pass


def add_notification_bot(name, platform, webhook_url, secret, enabled, events):
    """添加消息机器人配置
    
    Args:
        name: 机器人名称
        platform: 平台类型 'dingtalk' 或 'wecom'
        webhook_url: Webhook 地址
        enabled: 是否启用
        events: 订阅事件类型列表，如 ['mount_online', 'mount_offline']
    """
    with db_lock:
        conn = _get_conn()
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
                INSERT INTO notification_bots (name, platform, webhook_url, secret, enabled)
                VALUES (?, ?, ?, ?, ?)
            ''', (name, platform, webhook_url, secret, 1 if enabled else 0))
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
            pass


def update_notification_bot(bot_id, name, platform, webhook_url, secret, enabled, events):
    """更新消息机器人配置"""
    with db_lock:
        conn = _get_conn()
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
                SET name = ?, platform = ?, webhook_url = ?, secret = ?, enabled = ?
                WHERE id = ?
            ''', (name, platform, webhook_url, secret, 1 if enabled else 0, bot_id))
            
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
            pass


def delete_notification_bot(bot_id):
    """删除消息机器人配置"""
    with db_lock:
        conn = _get_conn()
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
            pass


def delete_user(user_id):
    """删除用户"""
    with db_lock:
        conn = _get_conn()
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
            pass

def get_all_users():
    """获取所有用户列表"""
    with db_lock:
        conn = _get_conn()
        c = conn.cursor()
        try:
            c.execute("SELECT id, username, password FROM users")
            return c.fetchall()
        finally:
            pass

def update_user_password(username, new_password):
    """更新用户密码"""
    with db_lock:
        conn = _get_conn()
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
            pass

def add_mount(mount, password, user_id=None):
    """添加新挂载点"""
    with db_lock:
        conn = _get_conn()
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
            pass

def update_mount(mount_id, mount=None, password=None, user_id=None):
    """更新挂载点信息"""
    with db_lock:
        conn = _get_conn()
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
            pass

def delete_mount(mount_id):
    """删除挂载点"""
    with db_lock:
        conn = _get_conn()
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
            pass

def get_all_mounts():
    """获取所有挂载点列表"""
    with db_lock:
        conn = _get_conn()
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
            pass



def get_mount_owner(mount_name):
    """获取挂载点所属用户"""
    with db_lock:
        conn = _get_conn()
        c = conn.cursor()
        try:
            c.execute('''
                SELECT u.username 
                FROM mounts m 
                LEFT JOIN users u ON m.user_id = u.id 
                WHERE m.mount = ?
            ''', (mount_name,))
            result = c.fetchone()
            return result[0] if result and result[0] else '未分配'
        except Exception:
            return '未知'
        finally:
            pass

def verify_admin(username, password):
    """验证管理员账号密码"""
    with db_lock:
        conn = _get_conn()
        c = conn.cursor()
        try:
            c.execute("SELECT password FROM admins WHERE username = ?", (username,))
            result = c.fetchone()
            if result and verify_password(result[0], password):
                return True
            return False
        finally:
            pass

def update_admin_password(username, new_password):
    """更新管理员密码"""
    with db_lock:
        conn = _get_conn()
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
            pass


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
        conn = _get_conn()
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
            pass


def get_connection_events_count(event_type=None, mount_name=None, username=None, start_time=None, end_time=None):
    """获取连接事件日志总数（用于分页）"""
    with db_lock:
        conn = _get_conn()
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
            pass


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
        conn = _get_conn()
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
            pass


# 连接事件日志允许排序的字段（白名单防 SQL 注入）
CONNECTION_EVENTS_SORT_FIELDS = {
    'event_time', 'event_type', 'mount_name', 'username', 'ip_address'
}


def get_connection_events(limit=100, offset=0, event_type=None, mount_name=None, username=None, start_time=None, end_time=None, sort_by='event_time', sort_order='DESC'):
    """获取连接事件日志列表

    Args:
        limit: 返回最大条数
        offset: 偏移量，用于分页
        event_type: 按事件类型筛选
        mount_name: 按挂载点名称筛选
        username: 按用户名筛选
        start_time: 开始时间（格式 'YYYY-MM-DD HH:MM:SS'）
        end_time: 结束时间（格式 'YYYY-MM-DD HH:MM:SS'）
        sort_by: 排序字段，必须在 CONNECTION_EVENTS_SORT_FIELDS 白名单内
        sort_order: 排序方向，'ASC' 或 'DESC'
    """
    # 白名单校验
    if sort_by not in CONNECTION_EVENTS_SORT_FIELDS:
        sort_by = 'event_time'
    sort_order = 'DESC' if str(sort_order).upper() != 'ASC' else 'ASC'

    with db_lock:
        conn = _get_conn()
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
            # ORDER BY 字段名是白名单常量，order 方向只能是 ASC/DESC，不存在 SQL 注入
            query += f' ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?'
            params.append(limit)
            params.append(offset)

            c.execute(query, params)
            rows = c.fetchall()
            
            result = []
            event_labels = {
                'base_station_online': '基站上线',
                'base_station_offline': '基站下线',
                'mount_online': '移动站上线',
                'mount_offline': '移动站下线'
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
            pass


def get_connection_events_statistics(start_time=None, end_time=None):
    """获取连接事件统计信息"""
    with db_lock:
        conn = _get_conn()
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
            pass


# ==================== 移动站 GGA 数据管理 ====================

def add_mobile_data_batch(rows):
    """批量插入移动站 GGA 数据

    重要：使用显式 BEGIN/COMMIT 把整个 executemany 包裹在一个事务里。
    连接设置为 autocommit (isolation_level=None)，如果不显式开启事务，
    executemany 中的每行都会被立即 fsync，导致 200 行批量插入耗时几十毫秒，
    长持 db_lock 阻塞 Web API 请求、造成页面卡顿。

    Args:
        rows: 可迭代对象，每个元素为 (event_time, username, mount_name, ip_address, nmea_type, raw_data, data_size, gga_quality)
              event_time 可为 None（由 DB 默认填充）；gga_quality 可为 None
    """
    if not rows:
        return 0
    with db_lock:
        conn = _get_conn()
        c = conn.cursor()
        try:
            conn.execute('BEGIN')
            c.executemany('''
                INSERT INTO mobile_station_data
                (event_time, username, mount_name, ip_address, nmea_type, raw_data, data_size, gga_quality)
                VALUES (COALESCE(?, datetime('now', 'localtime')), ?, ?, ?, ?, ?, ?, ?)
            ''', rows)
            conn.execute('COMMIT')
            return c.rowcount
        except Exception as e:
            try:
                conn.execute('ROLLBACK')
            except Exception:
                pass
            log_error(f'批量插入移动站数据失败: {e}', exc_info=True)
            return 0
        finally:
            pass


def get_mobile_data_count(username=None, mount_name=None, start_time=None, end_time=None, gga_quality=None):
    """获取移动站 GGA 数据总数（用于分页）

    gga_quality: 差分状态码过滤。
        - 整数 0-8：精确匹配
        - 'rtk' / 'differential'：常用聚合筛选（4/5 RTK 固定/浮点 + 2 DGPS）
    """
    with db_lock:
        conn = _get_conn()
        c = conn.cursor()
        try:
            query = 'SELECT COUNT(*) FROM mobile_station_data WHERE 1=1'
            params = []
            if username:
                query += ' AND username = ?'
                params.append(username)
            if mount_name:
                query += ' AND mount_name = ?'
                params.append(mount_name)
            if start_time:
                query += ' AND event_time >= ?'
                params.append(start_time)
            if end_time:
                query += ' AND event_time <= ?'
                params.append(end_time)
            if gga_quality is not None:
                _quality_clause, _quality_params = _build_quality_clause(gga_quality)
                query += ' AND ' + _quality_clause
                params.extend(_quality_params)
            c.execute(query, params)
            return c.fetchone()[0]
        finally:
            pass


def _build_quality_clause(gga_quality):
    """根据 gga_quality 参数构造 WHERE 子句 + 参数列表。

    支持的取值：
        - 'rtk' 或 'differential'：gga_quality IN (2, 4, 5) （DGPS + RTK 固定 + RTK 浮点）
        - 'fixed'：gga_quality = 4 (RTK 固定)
        - 'float'：gga_quality = 5 (RTK 浮点)
        - 'invalid'：gga_quality = 0
        - 'none'：gga_quality IS NULL
        - 整数 0-8 或可转 int 的字符串：精确匹配
    返回: (clause_string, params_list)
    """
    if gga_quality is None:
        return '1=1', []
    # 字符串预设
    s = str(gga_quality).strip().lower()
    if s in ('rtk', 'differential', 'diff'):
        return 'gga_quality IN (2, 4, 5)', []
    if s in ('fixed', 'rtk_fixed', 'fix'):
        return 'gga_quality = ?', [4]
    if s in ('float', 'rtk_float'):
        return 'gga_quality = ?', [5]
    if s in ('invalid', 'none_quality'):
        return 'gga_quality = ?', [0]
    if s in ('null', 'empty'):
        return 'gga_quality IS NULL', []
    # 整数 / 数字字符串：精确匹配
    try:
        q = int(s)
        if 0 <= q <= 8:
            return 'gga_quality = ?', [q]
    except (ValueError, TypeError):
        pass
    # 无法识别：不加过滤（返回所有）
    return '1=1', []


# 移动站 GGA 数据允许排序的字段（白名单防 SQL 注入）
MOBILE_DATA_SORT_FIELDS = {
    'event_time', 'username', 'mount_name'
}


def get_mobile_data(limit=100, offset=0, username=None, mount_name=None, start_time=None, end_time=None, gga_quality=None, sort_by='event_time', sort_order='DESC'):
    """获取移动站 GGA 数据列表

    Args:
        limit: 返回最大条数
        offset: 偏移量，用于分页
        username: 按用户名筛选
        mount_name: 按挂载点筛选
        start_time: 开始时间（格式 'YYYY-MM-DD HH:MM:SS'）
        end_time: 结束时间（格式 'YYYY-MM-DD HH:MM:SS'）
        gga_quality: 差分状态筛选。'rtk'/'fixed'/'float'/'invalid'/'null'/<0-8 整数>，详见 _build_quality_clause
        sort_by: 排序字段，必须在 MOBILE_DATA_SORT_FIELDS 白名单内
        sort_order: 排序方向，'ASC' 或 'DESC'
    """
    # 白名单校验
    if sort_by not in MOBILE_DATA_SORT_FIELDS:
        sort_by = 'event_time'
    sort_order = 'DESC' if str(sort_order).upper() != 'ASC' else 'ASC'

    with db_lock:
        conn = _get_conn()
        c = conn.cursor()
        try:
            query = '''
                SELECT id, event_time, username, mount_name, ip_address, nmea_type, raw_data, data_size, gga_quality
                FROM mobile_station_data
                WHERE 1=1
            '''
            params = []
            if username:
                query += ' AND username = ?'
                params.append(username)
            if mount_name:
                query += ' AND mount_name = ?'
                params.append(mount_name)
            if start_time:
                query += ' AND event_time >= ?'
                params.append(start_time)
            if end_time:
                query += ' AND event_time <= ?'
                params.append(end_time)
            if gga_quality is not None:
                _quality_clause, _quality_params = _build_quality_clause(gga_quality)
                query += ' AND ' + _quality_clause
                params.extend(_quality_params)
            # ORDER BY 字段名是白名单常量，order 方向只能是 ASC/DESC，不存在 SQL 注入
            query += f' ORDER BY {sort_by} {sort_order} LIMIT ? OFFSET ?'
            params.append(limit)
            params.append(offset)

            c.execute(query, params)
            rows = c.fetchall()

            result = []
            for row in rows:
                result.append({
                    'id': row[0],
                    'event_time': row[1],
                    'username': row[2],
                    'mount_name': row[3],
                    'ip_address': row[4],
                    'nmea_type': row[5],
                    'raw_data': row[6],
                    'data_size': row[7],
                    'gga_quality': row[8],
                })
            return result
        finally:
            pass


def cleanup_old_mobile_data(max_records=100000):
    """清理移动站 GGA 数据，按条数上限保留最新记录

    当 mobile_station_data 表总记录数超过 max_records 时，按 id 升序删除最旧的多余记录。
    id 为 INTEGER PRIMARY KEY 自增，单调递增，等价于按时间顺序裁剪且性能更优。

    Args:
        max_records: 最大保留条数；<= 0 表示禁用清理
    """
    if max_records <= 0:
        log_info('移动站数据保留条数配置无效或为 0，跳过清理')
        return 0

    with db_lock:
        conn = _get_conn()
        c = conn.cursor()
        try:
            c.execute('SELECT COUNT(*) FROM mobile_station_data')
            current_count = c.fetchone()[0]
            if current_count <= max_records:
                return 0

            overflow = current_count - max_records
            c.execute('''
                DELETE FROM mobile_station_data
                WHERE id IN (
                    SELECT id FROM mobile_station_data
                    ORDER BY id ASC
                    LIMIT ?
                )
            ''', (overflow,))
            deleted = c.rowcount
            conn.commit()
            if deleted > 0:
                log_info(f'已清理 {deleted} 条移动站数据（保留上限 {max_records} 条），当前 {current_count - deleted} 条')
                log_database_operation('cleanup_old_mobile_data', 'mobile_station_data', True, f'删除 {deleted} 条记录')
            return deleted
        except Exception as e:
            log_error(f'清理移动站数据失败: {e}', exc_info=True)
            log_database_operation('cleanup_old_mobile_data', 'mobile_station_data', False, str(e))
            return 0
        finally:
            pass


class DatabaseManager:
    """数据库管理器类，包装数据库操作函数"""
    
    def __init__(self):
        """初始化数据库管理器"""
        pass
    
    def init_database(self):
        """初始化数据库"""
        return init_db()
    
    def get_mount_owner(self, mount_name):
        """获取挂载点所属用户"""
        return get_mount_owner(mount_name)
    
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
        with db_lock:
            conn = _get_conn()
            c = conn.cursor()
            c.execute("SELECT password FROM users WHERE username = ?", (username,))
            result = c.fetchone()
            return result[0] if result else None
    
    def check_mount_exists_in_db(self, mount):
        """检查挂载点是否在数据库中存在"""
        with db_lock:
            conn = _get_conn()
            c = conn.cursor()
            c.execute("SELECT id FROM mounts WHERE mount = ?", (mount,))
            return c.fetchone() is not None
    
    def verify_download_user(self, mount, username, password):
        """验证下载用户，只验证用户名密码，不验证挂载点绑定关系"""
        with db_lock:
            conn = _get_conn()
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
            conn = _get_conn()
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
                pass
    
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
    
    def add_notification_bot(self, name, platform, webhook_url, secret, enabled, events):
        """添加消息机器人配置"""
        return add_notification_bot(name, platform, webhook_url, secret, enabled, events)
    
    def update_notification_bot(self, bot_id, name, platform, webhook_url, secret, enabled, events):
        """更新消息机器人配置"""
        return update_notification_bot(bot_id, name, platform, webhook_url, secret, enabled, events)
    
    def delete_notification_bot(self, bot_id):
        """删除消息机器人配置"""
        return delete_notification_bot(bot_id)
    
    def add_connection_event(self, event_type, mount_name=None, username=None, ip_address=None, duration=None, reason=None, details=None):
        """记录连接事件日志"""
        return add_connection_event(event_type, mount_name, username, ip_address, duration, reason, details)
    
    def get_connection_events(self, limit=100, offset=0, event_type=None, mount_name=None, username=None, start_time=None, end_time=None, sort_by='event_time', sort_order='DESC'):
        """获取连接事件日志列表"""
        return get_connection_events(limit, offset, event_type, mount_name, username, start_time, end_time, sort_by, sort_order)
    
    def get_connection_events_statistics(self, start_time=None, end_time=None):
        """获取连接事件统计"""
        return get_connection_events_statistics(start_time, end_time)
    
    def get_connection_events_count(self, event_type=None, mount_name=None, username=None, start_time=None, end_time=None):
        """获取连接事件日志总数"""
        return get_connection_events_count(event_type, mount_name, username, start_time, end_time)
    
    def cleanup_old_connection_events(self, retention_days=365, max_records=100000):
        """清理连接事件日志"""
        return cleanup_old_connection_events(retention_days, max_records)

    def add_mobile_data_batch(self, rows):
        """批量插入移动站 GGA 数据"""
        return add_mobile_data_batch(rows)

    def get_mobile_data(self, limit=100, offset=0, username=None, mount_name=None, start_time=None, end_time=None, gga_quality=None, sort_by='event_time', sort_order='DESC'):
        """获取移动站 GGA 数据列表"""
        return get_mobile_data(limit, offset, username, mount_name, start_time, end_time, gga_quality, sort_by, sort_order)

    def get_mobile_data_count(self, username=None, mount_name=None, start_time=None, end_time=None, gga_quality=None):
        """获取移动站 GGA 数据总数"""
        return get_mobile_data_count(username, mount_name, start_time, end_time, gga_quality)

    def cleanup_old_mobile_data(self, max_records=100000):
        """清理移动站 GGA 数据"""
        return cleanup_old_mobile_data(max_records)
    
