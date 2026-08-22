#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import signal
import logging
import psutil
import threading
import argparse
import os
from pathlib import Path
from threading import Thread

# 解析命令行参数
parser = argparse.ArgumentParser(description='2RTK NTRIP Caster')
parser.add_argument('--config', type=str, help='配置文件路径')
args = parser.parse_args()

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 如果指定了配置文件，设置环境变量
if args.config:
    os.environ['NTRIP_CONFIG_FILE'] = args.config

# 导入配置和核心模块
from src import config
from src import logger
from src import forwarder
from src.database import DatabaseManager
from src.web import create_web_manager
from src.ntrip import NTRIPCaster
from src.connection import get_connection_manager

def setup_logging():
    """设置日志系统"""
    # 初始化日志模块
    logger.init_logging()
    
    # 设置特定模块的日志级别
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('socketio').setLevel(logging.WARNING)
    logging.getLogger('engineio').setLevel(logging.WARNING)
    
    # 记录系统启动日志
    logger.log_system_event('日志系统初始化完成')

def print_banner():
    """打印启动横幅"""
    banner = f"""

    ██████╗ ██████╗ ████████╗██╗  ██╗
    ╚════██╗██╔══██╗╚══██╔══╝██║ ██╔╝
     █████╔╝██████╗╔   ██║   █████╔╝ 
    ██╔═══╝ ██╔══██╗   ██║   ██║  ██╗
    ███████╗██║  ██║   ██║   ██║  ██╗
    ╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝
    2RTK Ntrip Caster {config.VERSION}

NTRIP端口: {config.NTRIP_PORT:<8} Web管理端口: {config.WEB_PORT:<8} 
调试模式: {str(config.DEBUG):<9} 最大连接: {config.MAX_CONNECTIONS:<8} 

    """
    print(banner)

def check_environment():
    """检查运行环境"""
    logger = logging.getLogger('main')
    
    # 检查Python版本
    if sys.version_info < (3, 7):
        logger.error("需要Python 3.7或更高版本")
        sys.exit(1)
    
    # 检查必要的目录
    required_dirs = [
        Path(config.DATABASE_PATH).parent,
        Path(config.LOG_DIR)
    ]
    
    for dir_path in required_dirs:
        if not dir_path.exists():
            logger.info(f"创建目录: {dir_path}")
            dir_path.mkdir(parents=True, exist_ok=True)
    
    # 检查端口是否可用
    import socket
    
    def check_port(port, name):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # 在 Windows 上必须设置 SO_REUSEADDR，否则端口可能因 TIME_WAIT 状态或快速重启而被误判为占用
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('', port))
            return True
        except PermissionError as e:
            logger.error(f"{name}端口 {port} 无法绑定: {e}")
            logger.error(f"  可能原因：该端口被 Windows 系统保留/排除（如 Hyper-V、WSL2、Docker Desktop 预留范围），或被防火墙/安全软件拦截")
            logger.error(f"  解决方法：在 config.ini 中换一个不在系统排除范围内的端口（如 8080、8090、9000 等）")
            return False
        except OSError as e:
            logger.error(f"{name}端口 {port} 已被占用或不可用: {e}")
            return False
    
    ports_ok = True
    ports_ok &= check_port(config.NTRIP_PORT, "NTRIP")
    ports_ok &= check_port(config.WEB_PORT, "Web")
    
    if not ports_ok:
        logger.error("端口检查失败，请检查端口占用情况")
        sys.exit(1)
    
    logger.info("环境检查通过")

class ServiceManager:
    """服务管理器 - 统一管理所有服务组件"""
    
    def __init__(self):
        self.db_manager = None
        self.web_manager = None
        self.ntrip_caster = None
        self.web_thread = None
        self.running = False
        self.stopping = False  # 添加停止标志位，防止重复调用
        self.start_time = None
        self.stats_thread = None
        self.stats_interval = 10  # 统计打印间隔（秒）
        self.last_network_stats = None
        self.print_stats = False  # 控制是否在控制台打印统计信息
        self.system_stats_cache = {}  # 缓存系统统计数据供Web API使用
        self.cleanup_thread = None  # 连接事件日志清理线程
        self.cleanup_interval = 7 * 24 * 3600  # 清理间隔（秒），默认每周一次
        self.mobile_data_cleanup_thread = None  # 移动站 GGA 数据清理线程
        self.mobile_data_cleanup_interval = 3600  # 清理间隔（秒），默认每小时一次（按条数）
        
    def start_all_services(self):
        """启动所有服务"""
        try:
            self.start_time = time.time()
            logger.log_system_event(f'启动2RTK NTRIP Caster v{config.VERSION}')
            
            # 1. 初始化数据库
            self.db_manager = DatabaseManager()
            self.db_manager.init_database()
            logger.log_system_event('数据库初始化完成')
            
            # 2. 初始化并启动数据转发器
            forwarder.initialize()
            forwarder.start_forwarder()
            logger.log_system_event('数据转发器初始化完成')
            
            # 3. RTCM解析现在集成在connection_manager中，无需单独启动
            logger.log_system_event('RTCM解析器集成完成')
            
            # 4. 启动Web管理界面
            self._start_web_interface()
            
            # 5. 启动NTRIP服务器（在单独线程中）
            self.ntrip_caster = NTRIPCaster(self.db_manager)
            self.ntrip_thread = threading.Thread(target=self.ntrip_caster.start, daemon=True)
            self.ntrip_thread.start()
            time.sleep(1)  # 等待NTRIP服务器启动
            
            # 6. 注册信号处理
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            
            self.running = True
            logger.log_system_event(f'所有服务已启动 - NTRIP端口: {config.NTRIP_PORT}, Web端口: {config.WEB_PORT}')
            
            # 启动统计监控线程
            self._start_stats_monitor()

            # 立即在后台线程一次性预填统计缓存，避免首个Web请求等待
            def _seed_stats():
                try:
                    time.sleep(1)
                    if self.running:
                        self._update_system_stats()
                except Exception as e:
                    logger.log_error(f"预填统计缓存失败: {e}")

            Thread(target=_seed_stats, daemon=True).start()

            # 启动数据库清理线程（定时清理旧连接事件日志）
            self._start_cleanup_worker()

            # 启动移动站 GGA 数据清理线程（按条数上限清理）
            self._start_mobile_data_cleanup_worker()

            # 启动用户连接超时清理线程（防止 keep-alive 未检测到的网络断开）
            self._start_stale_user_cleanup_worker()
            
            # 主循环 - 保持服务运行
            self._main_loop()
            
        except Exception as e:
            logger.log_error(f"启动服务失败: {e}", exc_info=True)
            self.stop_all_services()
            raise
    
    def _start_web_interface(self):
        """启动Web管理界面"""
        from src.web import set_server_instance
        self.web_manager = create_web_manager(
            self.db_manager, 
            forwarder.get_forwarder(), 
            self.start_time
        )
        # 设置服务器实例供Web API使用
        set_server_instance(self)
        self.web_manager.start_rtcm_parsing()
        
        def run_web():
            self.web_manager.run(host=config.HOST, port=config.WEB_PORT, debug=False)
        
        self.web_thread = Thread(target=run_web, daemon=True)
        self.web_thread.start()
        
        # 显示所有可访问的Web管理界面地址
        web_urls = config.get_display_urls(config.WEB_PORT, "Web管理界面")
        if len(web_urls) == 1:
            logger.log_info(f'Web管理界面已启动，管理地址: {web_urls[0]}')
        else:
            logger.log_system_event('Web管理界面已启动，可通过以下地址访问:')
            for url in web_urls:
                logger.log_system_event(f'  - {url}')
    
    def _start_stats_monitor(self):
        """启动统计监控线程"""
        self.stats_thread = Thread(target=self._stats_monitor_worker, daemon=True)
        self.stats_thread.start()
    
    def _start_cleanup_worker(self):
        """启动数据库清理线程（定期删除超过保留期限的连接事件日志）"""
        self.cleanup_thread = Thread(target=self._cleanup_worker, daemon=True)
        self.cleanup_thread.start()
        logger.log_system_event(f'已启动连接事件日志清理线程，保留天数: {config.DB_RETENTION_DAYS}，清理周期: 每周一次')
    
    def _cleanup_worker(self):
        """数据库清理工作线程"""
        # 首次运行时立即执行一次清理，避免重启前积累的过期数据
        try:
            if self.db_manager:
                self.db_manager.cleanup_old_connection_events(config.DB_RETENTION_DAYS)
        except Exception as e:
            logger.log_error(f'首次清理连接事件日志失败: {e}', exc_info=True)

        while self.running:
            try:
                time.sleep(self.cleanup_interval)
                if self.running and self.db_manager:
                    self.db_manager.cleanup_old_connection_events(config.DB_RETENTION_DAYS)
            except Exception as e:
                logger.log_error(f'定时清理连接事件日志失败: {e}', exc_info=True)

    def _start_mobile_data_cleanup_worker(self):
        """启动移动站 GGA 数据清理线程（按条数上限）"""
        self.mobile_data_cleanup_thread = Thread(target=self._mobile_data_cleanup_worker, daemon=True)
        self.mobile_data_cleanup_thread.start()
        logger.log_system_event(f'已启动移动站 GGA 数据清理线程，保留条数: {config.MOBILE_DATA_MAX_RECORDS}，清理周期: 每小时一次')

    def _mobile_data_cleanup_worker(self):
        """移动站 GGA 数据清理工作线程（按条数上限裁剪）"""
        # 首次启动时立即执行一次，避免重启前积累过多记录
        try:
            if self.db_manager:
                self.db_manager.cleanup_old_mobile_data(config.MOBILE_DATA_MAX_RECORDS)
        except Exception as e:
            logger.log_error(f'首次清理移动站 GGA 数据失败: {e}', exc_info=True)

        while self.running:
            try:
                time.sleep(self.mobile_data_cleanup_interval)
                if self.running and self.db_manager:
                    self.db_manager.cleanup_old_mobile_data(config.MOBILE_DATA_MAX_RECORDS)
            except Exception as e:
                logger.log_error(f'定时清理移动站 GGA 数据失败: {e}', exc_info=True)

    def _start_stale_user_cleanup_worker(self):
        """启动超时用户连接清理线程（防止 keep-alive 未检测到的网络断开）"""
        self.stale_user_cleanup_thread = Thread(target=self._stale_user_cleanup_worker, daemon=True)
        self.stale_user_cleanup_thread.start()
        logger.log_system_event('已启动用户连接超时清理线程，清理周期: 每5分钟，深度探测每小时一次')

    def _stale_user_cleanup_worker(self):
        """用户连接超时清理工作线程

        每 5 分钟做一次轻量级清理（仅按 last_activity 时间判断），每小时做一次深度清理
        （主动 select+MSG_PEEK 探测 socket）。这样既不会消耗太多 CPU 也能及时回收
        因网络问题悄悄断开的用户连接，确保 mount_offline 事件不会丢失。
        """
        import time as _time
        idle_threshold = 300   # 5 分钟无活动视为可疑
        deep_check_interval = 12  # 每 12 * 5min = 1 小时做一次深度探测
        cycles = 0

        while self.running:
            try:
                _time.sleep(300)  # 5 分钟
                if not self.running:
                    break
                cycles += 1
                force = (cycles % deep_check_interval == 0)
                try:
                    cm = get_connection_manager()
                    cm.cleanup_stale_user_connections(
                        max_idle_seconds=idle_threshold,
                        force_check=force,
                    )
                except Exception as inner:
                    logger.log_error(f'超时清理用户连接失败: {inner}', exc_info=True)
            except Exception as e:
                logger.log_error(f'超时清理线程异常: {e}', exc_info=True)
                _time.sleep(60)

    def _stats_monitor_worker(self):
        """统计监控工作线程"""
        while self.running:
            try:
                time.sleep(self.stats_interval)
                if self.running:
                    self._update_system_stats()
            except Exception as e:
                logger.log_error(f"统计监控异常: {e}", exc_info=True)
    
    def _update_system_stats(self):
        """更新系统统计信息到缓存"""
        try:
            # 获取系统性能数据（interval=None 非阻塞，避免1秒阻塞）
            cpu_percent = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            
            # 获取网络统计
            network_stats = psutil.net_io_counters()
            network_bandwidth = self._calculate_network_bandwidth(network_stats)
            
            # 获取NTRIP服务器统计
            ntrip_stats = self.ntrip_caster.get_performance_stats() if self.ntrip_caster else {}
            
            # 获取连接管理器统计
            conn_manager = get_connection_manager()
            conn_stats = conn_manager.get_statistics()
            
            # 计算运行时间
            uptime = time.time() - self.start_time if self.start_time else 0
            uptime_str = self._format_uptime(uptime)
            
            # 计算数据传输统计
            total_data_bytes = sum(mount['total_bytes'] for mount in conn_stats.get('mounts', []) if 'total_bytes' in mount)
            total_data_mb = total_data_bytes / (1024 * 1024)
            
            # 更新缓存
            self.system_stats_cache = {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'uptime': uptime,  # 保存数字格式的运行时间
                'uptime_str': uptime_str,  # 保存格式化的运行时间字符串
                'cpu_percent': cpu_percent,
                'memory': memory,
                'network_bandwidth': network_bandwidth,
                'ntrip_stats': ntrip_stats,
                'conn_stats': conn_stats,
                'total_data_mb': total_data_mb
            }
            
        except Exception as e:
            logger.log_error(f"更新统计信息失败: {e}", exc_info=True)
    

    
    def get_system_stats(self):
        """获取系统统计数据供Web API使用"""
        try:
            # 缓存为空时只需返回空数据，由后台统计线程负责填充，避免阻塞Web请求线程
            stats = self.system_stats_cache.copy()
            if not stats:
                logger.log_info("系统统计缓存为空，等待后台线程填充")
                return {}
            
            # 格式化数据供前端使用
            if stats:
                memory_info = stats.get('memory')
                network_info = stats.get('network_bandwidth', {})
                
                return {
                    'timestamp': stats.get('timestamp'),
                    'uptime': stats.get('uptime', 0),  # 返回数字格式的运行时间
                    'cpu_percent': round(stats.get('cpu_percent', 0), 1),
                    'memory': {
                        'percent': round(getattr(memory_info, 'percent', 0), 1),
                        'used': getattr(memory_info, 'used', 0),
                        'total': getattr(memory_info, 'total', 0)
                    },
                    'network_bandwidth': {
                        'sent_rate': network_info.get('sent_rate', 0) if isinstance(network_info, dict) else 0,
                        'recv_rate': network_info.get('recv_rate', 0) if isinstance(network_info, dict) else 0
                    },
                    'connections': {
                        'active': stats.get('ntrip_stats', {}).get('active_connections', 0),
                        'total': stats.get('ntrip_stats', {}).get('total_connections', 0),
                        'rejected': stats.get('ntrip_stats', {}).get('rejected_connections', 0),
                        'max_concurrent': stats.get('ntrip_stats', {}).get('max_concurrent', 0)
                    },
                    'mounts': stats.get('conn_stats', {}).get('mounts', {}),
                    'users': stats.get('conn_stats', {}).get('users', {}),
                    'data_transfer': {
                        'total_bytes': stats.get('total_data_mb', 0) * 1024 * 1024
                    }
                }
            return {}
        except Exception as e:
            logger.log_error(f"获取系统统计数据失败: {e}", exc_info=True)
            return {}
    
    def set_print_stats(self, enabled):
        """设置是否在控制台打印统计信息"""
        self.print_stats = enabled
        if enabled:
            logger.log_system_event('已启用控制台统计信息打印')
        else:
            logger.log_system_event('已禁用控制台统计信息打印')
    
    def _calculate_network_bandwidth(self, current_stats):
        """计算网络带宽"""
        if self.last_network_stats is None:
            self.last_network_stats = (current_stats, time.time())
            return "计算中..."
        
        last_stats, last_time = self.last_network_stats
        current_time = time.time()
        time_diff = current_time - last_time
        
        if time_diff <= 0:
            return "计算中..."
        
        bytes_sent_diff = current_stats.bytes_sent - last_stats.bytes_sent
        bytes_recv_diff = current_stats.bytes_recv - last_stats.bytes_recv
        
        upload_mbps = (bytes_sent_diff * 8) / (time_diff * 1024 * 1024)
        download_mbps = (bytes_recv_diff * 8) / (time_diff * 1024 * 1024)
        total_mbps = upload_mbps + download_mbps
        
        self.last_network_stats = (current_stats, current_time)
        
        return f"↑{upload_mbps:.2f} Mbps ↓{download_mbps:.2f} Mbps (总计: {total_mbps:.2f} Mbps)"
    
    def _format_uptime(self, seconds):
        """格式化运行时间"""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if days > 0:
            return f"{days}天 {hours}小时 {minutes}分钟"
        elif hours > 0:
            return f"{hours}小时 {minutes}分钟"
        else:
            return f"{minutes}分钟 {secs}秒"

    def _main_loop(self):
        """主循环 - 监控服务状态"""
        while self.running:
            try:
                # 检查各服务状态
                if self.ntrip_caster and not self.ntrip_caster.running:
                    logger.log_error('NTRIP服务器意外停止')
                    break
                    
                if self.web_thread and not self.web_thread.is_alive():
                    logger.log_error('Web服务意外停止')
                    break
                
                # 短暂休眠避免CPU占用过高
                time.sleep(1)
                
            except Exception as e:
                logger.log_error(f"主循环异常: {e}", exc_info=True)
                break
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        if self.stopping:
            logger.log_system_event(f'收到信号 {signum}，但服务正在关闭中，忽略重复信号')
            return
        logger.log_system_event(f'收到信号 {signum}，开始关闭所有服务')
        self.stop_all_services()
    
    def stop_all_services(self):
        """停止所有服务"""
        if self.stopping:
            logger.log_system_event('服务正在关闭中，避免重复调用')
            return
            
        self.stopping = True
        logger.log_system_event('正在关闭所有服务')
        
        try:
            self.running = False
            
            # 等待统计监控线程结束
            if self.stats_thread and self.stats_thread.is_alive():
                logger.log_system_event('正在停止统计监控线程')
                self.stats_thread.join(timeout=2)
            
            # 停止NTRIP服务器
            if self.ntrip_caster:
                try:
                    self.ntrip_caster.stop()
                except Exception as e:
                    logger.log_error(f'停止NTRIP服务器时出错: {e}')
        
            # 停止数据转发器
            try:
                forwarder.stop_forwarder()
            except Exception as e:
                logger.log_error(f'停止数据转发器时出错: {e}')
            
            # 停止Web管理器
            if self.web_manager:
                try:
                    self.web_manager.stop_rtcm_parsing()
                except Exception as e:
                    logger.log_error(f'停止Web管理器时出错: {e}')
            
            logger.log_system_event('所有服务已关闭')
            
        except Exception as e:
            logger.log_error(f'关闭服务时发生异常: {e}')
        finally:
            # 确保停止标志位被重置（虽然程序即将退出）
            self.stopping = False

# 全局服务器实例
server = None

def get_server_instance():
    """获取服务器实例"""
    return server

def main():
    """主函数"""
    global server
    try:
        # 设置日志
        setup_logging()
        main_logger = logger.get_logger('main')
        
        # 打印启动信息
        print_banner()
        
        # 检查环境
        check_environment()
        
        # 初始化配置
        config.init_config()
        logger.log_system_event('配置初始化完成')
        
        # 创建服务器实例并启动所有服务
        server = ServiceManager()
        globals()['server'] = server  # 设置全局变量
        server.start_all_services()
        
    except KeyboardInterrupt:
        logger.log_system_event('收到中断信号，正在关闭服务')
    except Exception as e:
        logger.log_error(f"启动失败: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if server:
            server.stop_all_services()
        logger.log_system_event('程序已退出')
        logger.shutdown_logging()

if __name__ == '__main__':
    main()