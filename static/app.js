// Socket.IO connection configuration
const socket = io({
    timeout: 20000,
    forceNew: false,
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    maxReconnectionAttempts: 5
});
let currentPage = 'dashboard';
let connectionStatus = 'connecting';

// WebSocket connection status monitoring
socket.on('connect', function() {
    connectionStatus = 'connected';
    // console.log('WebSocket connection established');
    updateConnectionStatus();
    // 重连后重新加入RTCM实时视图房间（房间绑定的是连接，断开后失效）
    if (currentMountName) {
        socket.emit('join_rtcm_room', { mount: currentMountName });
    }
    // If currently on dashboard page, request system statistics
    if (currentPage === 'dashboard') {
        requestSystemStats();
    }
});

socket.on('disconnect', function(reason) {
    connectionStatus = 'disconnected';
    // console.log('WebSocket connection disconnected:', reason);
    updateConnectionStatus();
});

socket.on('reconnect', function(attemptNumber) {
    connectionStatus = 'connected';
    // console.log('WebSocket reconnection successful, attempt number:', attemptNumber);
    updateConnectionStatus();
    // 重连成功后重新加入RTCM实时视图房间
    if (currentMountName) {
        socket.emit('join_rtcm_room', { mount: currentMountName });
    }
});

socket.on('reconnect_attempt', function(attemptNumber) {
    connectionStatus = 'reconnecting';
    // console.log('WebSocket reconnection attempt:', attemptNumber);
    updateConnectionStatus();
});

socket.on('reconnect_failed', function() {
    connectionStatus = 'failed';
    // console.log('WebSocket reconnection failed');
    updateConnectionStatus();
});

// Update connection status display
// GGA定位质量码映射表
const GGA_QUALITY_MAP = {
    0: { label: '0-无效', color: '#dc3545' },
    1: { label: '1-单点', color: '#6c757d' },
    2: { label: '2-DGPS', color: '#17a2b8' },
    3: { label: '3-PPS', color: '#ffc107' },
    4: { label: '4-RTK固定', color: '#28a745' },
    5: { label: '5-RTK浮点', color: '#20c997' },
    6: { label: '6-估算', color: '#fd7e14' },
    7: { label: '7-人工', color: '#6f42c1' },
    8: { label: '8-模拟', color: '#e83e8c' }
};

function updateConnectionStatus() {
    const statusElement = document.getElementById('connection-status');
    if (statusElement) {
        const statusText = {
        'connecting': '连接中...',
        'connected': '已连接',
        'disconnected': '已断开',
        'reconnecting': '重连中...',
        'failed': '连接失败'
    };
        const statusColor = {
            'connecting': '#ffd93d',
            'connected': '#00ff41',
            'disconnected': '#ff6b6b',
            'reconnecting': '#ffd93d',
            'failed': '#ff6b6b'
        };
        statusElement.textContent = statusText[connectionStatus] || '未知状态';
        statusElement.style.color = statusColor[connectionStatus] || '#adb5bd';
    }
}

// Page navigation
function navigateTo(page) {
    // Check pages that require login
    const requireLoginPages = ['dashboard', 'users', 'mounts', 'connection_events', 'settings', 'data_view'];
    if (requireLoginPages.includes(page)) {
        // Check login status
        checkLoginStatusForProtectedPage().then(isLoggedIn => {
            if (!isLoggedIn) {
                // Redirect to login page with target page parameter
                window.location.href = `/login?redirect=${page}`;
                return;
            }
            // Logged in, continue navigation
            performNavigation(page);
        });
    } else {
        // Navigate directly for pages that don't require login
        performNavigation(page);
    }
}

// Execute actual page navigation
function performNavigation(page) {
    // Update navigation state
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    document.querySelector(`[data-page="${page}"]`).classList.add('active');

    currentPage = page;

    // Control log panel display
    const logPanel = document.getElementById('log-panel');
    const mainContent = document.querySelector('.main-content');
    const contentPanel = document.querySelector('.content-panel');

    if (page === 'dashboard') {
        logPanel.style.display = 'block';
        mainContent.classList.add('dashboard-layout');
    } else {
        logPanel.style.display = 'none';
        mainContent.classList.remove('dashboard-layout');
    }

    // 列表型页面：连接事件 / 数据查看，加 list-page 类让内部表格滚动而非整页滚动
    const listPages = ['data_view', 'connection_events'];
    if (listPages.includes(page)) {
        contentPanel.classList.add('list-page');
    } else {
        contentPanel.classList.remove('list-page');
    }

    loadPageContent(page);
}

// Check login status (for protected pages)
async function checkLoginStatusForProtectedPage() {
    try {
        const response = await fetch('/api/auth/check');
        const data = await response.json().catch(() => ({}));
        return response.ok && data.authenticated === true;
    } catch (error) {
        // console.error('Failed to check login status:', error);
        return false;
    }
}

// Check login status (original function, maintain compatibility)
async function checkLoginStatus() {
    try {
        const response = await fetch('/api/auth/check');
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.authenticated !== true) {
            showAlert('登录已过期，请重新登录', 'warning');
            window.location.href = '/login';
            return false;
        }
        return true;
    } catch (error) {
        // console.error('检查登录状态失败:', error);
        return false;
    }
}

// Handle API response
async function handleApiResponse(response, skipAuthRedirect = false) {
    if (response.status === 401) {
        if (!skipAuthRedirect) {
            showAlert('登录已过期，请重新登录', 'warning');
            window.location.href = '/login';
        }
        throw new Error('Unauthorized access');
    }
    
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({ error: '未知错误' }));
        throw new Error(errorData.error || `HTTP ${response.status}`);
    }
    
    return response.json();
}

// Handle API response (for public pages, won't auto-redirect to login)
// Load page content
async function loadPageContent(page) {
    const contentDiv = document.getElementById('page-content');
    
    try {
        let response;
        switch(page) {
            case 'dashboard':
                contentDiv.innerHTML = getDashboardContent();
                // Show content panel for dashboard page
                contentDiv.parentElement.style.display = 'block';
                // Load system statistics
                fetchSystemStats();
                // Request real-time data
                requestSystemStats();
                break;
        case 'users': {
            // Ensure content panel is displayed on non-dashboard pages
            contentDiv.parentElement.style.display = 'block';
            const params = new URLSearchParams();
            if (window.mobileStationMountFilter) {
                params.append('mount_name', window.mobileStationMountFilter);
            }
            if (window.mobileStationUsernameFilter) {
                params.append('username', window.mobileStationUsernameFilter);
            }
            const url = '/api/users' + (params.toString() ? '?' + params.toString() : '');
            response = await fetch(url);
            const connections = await handleApiResponse(response);
            contentDiv.innerHTML = getUsersContent(connections);
            loadAnonymousSetting();
            break;
        }
            case 'mounts':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                response = await fetch('/api/mounts');
                const mounts = await handleApiResponse(response);
                // /api/mounts API already contains correct online status and connection count information, use directly
                contentDiv.innerHTML = getMountsContent(mounts);
                loadAnonymousSetting();
                break;
            case 'monitor':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                contentDiv.innerHTML = getMonitorContent();
                // Update monitoring data display immediately
                updateMonitorData();
                // Add INFO button event handling for STR items
                setTimeout(() => {
                    addInfoButtonsToSTRItems();
                }, 200);
                // Initialize map when monitor page is loaded
                setTimeout(() => {
                    initializeMapForMonitor();
                }, 300);
                break;
            case 'connection_events':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                contentDiv.innerHTML = getConnectionEventsContent();
                loadConnectionEvents(0, 100);
                break;
            case 'data_view':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                contentDiv.innerHTML = getDataViewContent();
                loadMobileData(0, 100);
                break;
            case 'settings':
                // Ensure content panel is displayed on non-dashboard pages
                contentDiv.parentElement.style.display = 'block';
                contentDiv.innerHTML = getSettingsContent();
                loadNotificationBots();
                break;
        }
    } catch (error) {
        // console.error('加载页面内容失败:', error);
        contentDiv.innerHTML = '<div class="error-message">页面加载失败，请稍后重试。</div>';
    }
}

// Add INFO button event handling for STR items
function addInfoButtonsToSTRItems() {
    const infoButtons = document.querySelectorAll('.str-info-btn');
    
    infoButtons.forEach(button => {
        // Avoid duplicate event binding
        if (button.hasAttribute('data-event-bound')) {
            return;
        }
        
        const mountName = button.getAttribute('data-mount');
        if (!mountName) {
            return;
        }
        
        button.title = `查看挂载点 ${mountName} 的实时 RTCM 解析数据`;
        
        button.addEventListener('click', () => {
            // Start RTCM parsing and update container content
            startRTCMParsing(mountName);
        });
        
        // Mark as event bound
        button.setAttribute('data-event-bound', 'true');
    });
}

// Start RTCM parsing
// Store last position information for position change detection
let lastPosition = { latitude: null, longitude: null };
// Store map center for distance comparison
let mapCenter = { latitude: null, longitude: null };
// Track if this is the first marking
let isFirstMarking = true;
// Track if map was switched to force re-marking
let mapSwitched = false;
// Store current mount name for map display
let currentMountName = null;

function startRTCMParsing(mountName) {
    console.log(`[前端] 开始启动RTCM解析: ${mountName}`);
    
    // 
    fetch('/api/mount/rtcm-parse/status')
    .then(response => response.json())
    .then(statusData => {
        if (statusData.success) {
            const status = statusData.status;
            console.log(`[前端] 当前解析器状态:`, status);
            console.log(`[前端] 当前活跃Web挂载点: ${status.current_web_mount || '无'}`);
            console.log(`[前端] Web解析线程数: ${status.web_parsers}, STR解析线程数: ${status.str_parsers}`);
            
            if (status.current_web_mount && status.current_web_mount !== mountName) {
                console.log(`[前端] 检测到前一个活跃挂载点: ${status.current_web_mount}，将被自动清理`);
            }
        }
    })
    .catch(error => {
        console.warn(`[前端] 获取解析器状态失败:`, error);
    });
    
    // Reset marking status for new mount point
    isFirstMarking = true;
    mapSwitched = false;
    lastPosition = { latitude: null, longitude: null };
    mapCenter = { latitude: null, longitude: null };
    
    // Update base station information container
    updateStationInfo(mountName);
    
    // Initialize satellite visualization
    initializeSatelliteVisualization();
    
    // Call backend API to start RTCM parsing
    console.log(`[前端] 调用后端API启动RTCM解析: ${mountName}`);
    fetch(`/api/mount/${mountName}/rtcm-parse/start`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log(`[前端] RTCM解析启动成功: ${mountName}`);
            // 加入RTCM实时视图房间，接收rtcm_realtime_data推送
            if (socket && socket.connected) {
                socket.emit('join_rtcm_room', { mount: mountName });
                console.log(`[前端] 已加入RTCM实时视图房间: ${mountName}`);
            }
            // 
            setTimeout(() => {
                fetch('/api/mount/rtcm-parse/status')
                .then(response => response.json())
                .then(statusData => {
                    if (statusData.success) {
                        console.log(`[前端] 启动后解析器状态:`, statusData.status);
                    }
                })
                .catch(error => console.warn(`[前端] 获取启动后状态失败:`, error));
            }, 1000);
        } else {
            console.error(`[前端] RTCM解析启动失败: ${data.error || '未知错误'}`);
            showAlert(`启动 RTCM 解析失败：${data.error || '未知错误'}`, 'error');
        }
    })
    .catch(error => {
        console.error('[前端] 调用RTCM解析API失败:', error);
        showAlert('调用 RTCM 解析接口失败', 'error');
    });
}

// Calculate distance between two points (meters)
function calculateDistance(lat1, lon1, lat2, lon2) {
    const R = 6371000; // Earth radius (meters)
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

// Handle position updates, determine if re-marking is needed
function handlePositionUpdate(latitude, longitude, mountName = null) {
    // Force re-marking if map was switched
    if (mapSwitched) {
        mapSwitched = false;
        lastPosition.latitude = latitude;
        lastPosition.longitude = longitude;
        updateMapLocation(latitude, longitude, mountName, false); // Second marking, don't fix zoom
        return;
    }
    
    // First time marking - always mark and set zoom to 8
    if (isFirstMarking) {
        isFirstMarking = false;
        lastPosition.latitude = latitude;
        lastPosition.longitude = longitude;
        mapCenter.latitude = latitude;
        mapCenter.longitude = longitude;
        updateMapLocation(latitude, longitude, mountName, true); // First marking, set zoom to 8
        return;
    }
    
    // Check both distance conditions - update marker if either condition is met
    let shouldUpdateMarker = false;
    let updateReason = '';
    
    // Check distance from last position (500m threshold)
    if (lastPosition.latitude !== null && lastPosition.longitude !== null) {
        const distance = calculateDistance(
            lastPosition.latitude, lastPosition.longitude,
            latitude, longitude
        );
        
        // If position change is 500 meters or more, should update marker
        if (distance >= 500) {
            shouldUpdateMarker = true;
            updateReason = `position change ${distance.toFixed(1)}m >= 500m threshold`;
        }
    }
    
    // Check distance from map center (50km threshold)
    if (mapCenter.latitude !== null && mapCenter.longitude !== null) {
        const centerDistance = calculateDistance(
            mapCenter.latitude, mapCenter.longitude,
            latitude, longitude
        );
        
        // If distance from map center is 50km or more, should update marker
        if (centerDistance >= 50000) {
            shouldUpdateMarker = true;
            updateReason = `distance from map center ${(centerDistance/1000).toFixed(1)}km >= 50km threshold`;
            // Update map center when re-marking due to distance
            mapCenter.latitude = latitude;
            mapCenter.longitude = longitude;
        }
    }
    
    // Check if marker is visible in current map view
    if (currentMap && !shouldUpdateMarker) {
        const view = currentMap.getView();
        const extent = view.calculateExtent(currentMap.getSize());
        const markerCoord = ol.proj.fromLonLat([longitude, latitude]);
        
        // If marker is not within current view extent, should update marker
        if (!ol.extent.containsCoordinate(extent, markerCoord)) {
            shouldUpdateMarker = true;
            updateReason = 'marker not visible in current map view';
            // Update map center to current marker position
            const currentCenter = ol.proj.toLonLat(view.getCenter());
            mapCenter.latitude = currentCenter[1];
            mapCenter.longitude = currentCenter[0];
        }
    }
    
    // If neither condition is met, don't update marker
    if (!shouldUpdateMarker) {
        // console.log(`No update needed - position and center distance within thresholds`);
        return;
    }
    
    // console.log(`Updating marker: ${updateReason}`);
    
    // Update position and mark
    lastPosition.latitude = latitude;
    lastPosition.longitude = longitude;
    updateMapLocation(latitude, longitude, mountName, false); // Subsequent marking, don't fix zoom
}

// Update base station information
function updateStationInfo(mountName) {
    const stationInfoDiv = document.getElementById('station-info');
    stationInfoDiv.innerHTML = `
        <div class="station-info-loading">
            <p>正在解析挂载点 ${mountName} 的 RTCM 数据...</p>
            <div class="loading-spinner"></div>
        </div>
    `;
    
    // 基准站信息通过实时数据展示
}

// Display base station information
function displayStationInfo(stationData) {
    const stationInfoDiv = document.getElementById('station-info');
    stationInfoDiv.innerHTML = `
        <div class="station-details">
            <!-- 第一行：基本信息 -->
            <div class="info-row-group">
                <div class="info-row">
                    <span class="info-label">挂载点：</span>
                    <span class="info-value" id="station-name">${stationData.name || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">基准站 ID：</span>
                    <span class="info-value" id="station-id">${stationData.id || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">国家：</span>
                    <span class="info-value" id="station-country">${stationData.country_name || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">城市：</span>
                    <span class="info-value" id="station-city">${stationData.city || '未知'}</span>
                </div>
            </div>
            
            <!-- 第二行：设备信息 -->
            <div class="info-row-group">
                <div class="info-row">
                    <span class="info-label">接收机类型：</span>
                    <span class="info-value" id="receiver-type">${stationData.receiver?.name || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">接收机固件：</span>
                    <span class="info-value" id="receiver-version">${stationData.receiver?.firmware || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">天线类型：</span>
                    <span class="info-value" id="antenna-type">${stationData.antenna?.name || '未知'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">天线序列号：</span>
                    <span class="info-value" id="antenna-serial">${stationData.antenna?.serial || '未知'}</span>
                </div>
            </div>
            
            <!-- 第三行：坐标信息 -->
            <div class="info-row-group coordinates-group">
                <div class="coordinates-half">
                    <div class="info-row">
                        <span class="info-label">坐标：</span>
                        <span class="info-value">经度：<span id="station-longitude">${stationData.longitude || 0}</span>°，纬度：<span id="station-latitude">${stationData.latitude || 0}</span>°，高度：<span id="station-height">${stationData.height || '未知'}</span></span>
                    </div>
                </div>
                <div class="coordinates-half">
                    <div class="info-row">
                        <span class="info-label">地心地固坐标：</span>
                        <span class="info-value" id="station-xyz">X：${stationData.x || 0}，Y：${stationData.y || 0}，Z：${stationData.z || 0}</span>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // Update base station status
    updateStationStatus(true);
    
    // Hide map loading overlay
    const mapOverlay = document.getElementById('map-loading');
    if (mapOverlay) {
        mapOverlay.style.display = 'none';
    }
}

// Map related variables
let currentMap = null;
let mapType = 'amap'; // 'amap' or 'osm'
let stationMarker = null;
let coverageCircles = [];

// Initialize map
function initializeMap() {
    // Set map switch button events
    const amapBtn = document.getElementById('amap-btn');
    const osmBtn = document.getElementById('osm-btn');
    
    amapBtn.addEventListener('click', () => switchToAmap());
    osmBtn.addEventListener('click', () => switchToOSM());
    
    // Load map library by default
    loadMapLibrary();
    
    // Position data is now updated through simulated data
}

// Initialize map specifically for monitor page
function initializeMapForMonitor() {
    console.log('[地图初始化] 开始初始化monitor页面地图');
    
    // Check if we're on monitor page and map container exists
    if (currentPage !== 'monitor') {
        console.log('[地图初始化] 不在monitor页面，跳过地图初始化');
        return;
    }
    
    const mapContainer = document.getElementById('map');
    if (!mapContainer) {
        console.log('[地图初始化] 地图容器不存在，跳过初始化');
        return;
    }
    
    // Set map switch button events (re-bind after page reload)
    const amapBtn = document.getElementById('amap-btn');
    const osmBtn = document.getElementById('osm-btn');
    
    if (amapBtn && osmBtn) {
        // Remove existing event listeners to avoid duplicates
        amapBtn.replaceWith(amapBtn.cloneNode(true));
        osmBtn.replaceWith(osmBtn.cloneNode(true));
        
        // Re-get elements after replacement
        const newAmapBtn = document.getElementById('amap-btn');
        const newOsmBtn = document.getElementById('osm-btn');
        
        newAmapBtn.addEventListener('click', () => switchToAmap());
        newOsmBtn.addEventListener('click', () => switchToOSM());
        
        console.log('[地图初始化] 地图切换按钮事件已重新绑定');
    }
    
    // Force re-initialize map
    if (typeof ol !== 'undefined') {
        console.log('[地图初始化] OpenLayers已加载，直接初始化地图');
        initMap();
        
        // If we have previous position data, restore the marker
        if (lastPosition.latitude !== null && lastPosition.longitude !== null) {
            console.log('[地图初始化] 恢复之前的位置标记:', lastPosition);
            // Force re-marking without distance check
            updateMapLocation(lastPosition.latitude, lastPosition.longitude, currentMountName, false);
        }
    } else {
        console.log('[地图初始化] OpenLayers未加载，开始加载库');
        loadMapLibrary();
    }
}

// Load OpenLayers map library
function loadMapLibrary() {
    if (typeof ol === 'undefined') {
        // Dynamically load OpenLayers CSS
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = 'https://cdn.jsdelivr.net/npm/ol@v7.5.2/ol.css';
        document.head.appendChild(link);
        
        // Dynamically load OpenLayers JS
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/ol@v7.5.2/dist/ol.js';
        script.onload = () => initMap();
        document.head.appendChild(script);
    } else {
        initMap();
    }
}

// Initialize map
function initMap() {
    console.log('[地图初始化] 开始创建地图实例');
    
    // Check if map container exists
    const mapContainer = document.getElementById('map');
    if (!mapContainer) {
        console.log('[地图初始化] 地图容器不存在，无法创建地图');
        return;
    }
    
    // Clean up existing map
    if (currentMap) {
        console.log('[地图初始化] 清理现有地图实例');
        currentMap.setTarget(null);
        currentMap = null;
    }
    
    // Create layer based on current map type
    const layer = mapType === 'amap' ? createAmapLayer() : createOSMLayer();
    
    try {
        currentMap = new ol.Map({
            target: 'map',
            layers: [layer],
            view: new ol.View({
                center: ol.proj.fromLonLat([110.277492, 25.20341154]),
                zoom: 8
            })
        });
        
        // Create marker layer
        const markerLayer = new ol.layer.Vector({
            source: new ol.source.Vector()
        });
        currentMap.addLayer(markerLayer);
        
        console.log('[地图初始化] 地图实例创建成功');
        updateMapButtons();
    } catch (error) {
        console.error('[地图初始化] 创建地图实例失败:', error);
    }
}

// Create Amap layer
function createAmapLayer() {
    return new ol.layer.Tile({
        source: new ol.source.XYZ({
            url: 'https://wprd01.is.autonavi.com/appmaptile?x={x}&y={y}&z={z}&lang=zh_cn&size=1&scl=1&style=7',
            crossOrigin: 'anonymous'
        })
    });
}

// Create OSM layer
function createOSMLayer() {
    return new ol.layer.Tile({
        source: new ol.source.OSM()
    });
}

// Switch to Amap
function switchToAmap() {
    if (mapType !== 'amap') {
        mapType = 'amap';
        mapSwitched = true; // Mark that map was switched
        initMap();
        // If we have position data, force re-marking
        if (lastPosition.latitude !== null && lastPosition.longitude !== null) {
            handlePositionUpdate(lastPosition.latitude, lastPosition.longitude);
        }
    }
}

// Switch to OSM
function switchToOSM() {
    if (mapType !== 'osm') {
        mapType = 'osm';
        mapSwitched = true; // Mark that map was switched
        initMap();
        // If we have position data, force re-marking
        if (lastPosition.latitude !== null && lastPosition.longitude !== null) {
            handlePositionUpdate(lastPosition.latitude, lastPosition.longitude);
        }
    }
}

// Update map button status
function updateMapButtons() {
    const amapBtn = document.getElementById('amap-btn');
    const osmBtn = document.getElementById('osm-btn');
    
    if (mapType === 'amap') {
        amapBtn.className = 'btn btn-primary btn-sm';
        osmBtn.className = 'btn btn-secondary btn-sm';
    } else {
        amapBtn.className = 'btn btn-secondary btn-sm';
        osmBtn.className = 'btn btn-primary btn-sm';
    }
}


// 坐标转换函数：WGS84转GCJ02（火星坐标系）
function wgs84ToGcj02(lng, lat) {
    const x_pi = 3.14159265358979324 * 3000.0 / 180.0;
    const pi = 3.1415926535897932384626;
    const a = 6378245.0; // 长半轴
    const ee = 0.00669342162296594323; // 扁率
    
    // 判断是否在中国境外
    function outOfChina(lng, lat) {
        return (lng < 72.004 || lng > 137.8347) || (lat < 0.8293 || lat > 55.8271);
    }
    
    function transformLat(lng, lat) {
        let ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * Math.sqrt(Math.abs(lng));
        ret += (20.0 * Math.sin(6.0 * lng * pi) + 20.0 * Math.sin(2.0 * lng * pi)) * 2.0 / 3.0;
        ret += (20.0 * Math.sin(lat * pi) + 40.0 * Math.sin(lat / 3.0 * pi)) * 2.0 / 3.0;
        ret += (160.0 * Math.sin(lat / 12.0 * pi) + 320 * Math.sin(lat * pi / 30.0)) * 2.0 / 3.0;
        return ret;
    }
    
    function transformLng(lng, lat) {
        let ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * Math.sqrt(Math.abs(lng));
        ret += (20.0 * Math.sin(6.0 * lng * pi) + 20.0 * Math.sin(2.0 * lng * pi)) * 2.0 / 3.0;
        ret += (20.0 * Math.sin(lng * pi) + 40.0 * Math.sin(lng / 3.0 * pi)) * 2.0 / 3.0;
        ret += (150.0 * Math.sin(lng / 12.0 * pi) + 300.0 * Math.sin(lng / 30.0 * pi)) * 2.0 / 3.0;
        return ret;
    }
    
    // 如果在中国境外，不进行转换
    if (outOfChina(lng, lat)) {
        return [lng, lat];
    }
    
    let dlat = transformLat(lng - 105.0, lat - 35.0);
    let dlng = transformLng(lng - 105.0, lat - 35.0);
    const radlat = lat / 180.0 * pi;
    let magic = Math.sin(radlat);
    magic = 1 - ee * magic * magic;
    const sqrtmagic = Math.sqrt(magic);
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi);
    dlng = (dlng * 180.0) / (a / sqrtmagic * Math.cos(radlat) * pi);
    const mglat = lat + dlat;
    const mglng = lng + dlng;
    return [mglng, mglat];
}

function updateMapLocation(latitude, longitude, mountName = null, isInitialMarking = false) {
    if (!currentMap) return;
    
    // 根据地图类型决定是否进行坐标转换
    let displayLng = longitude;
    let displayLat = latitude;
    
    // 如果是高德地图，需要将WGS84坐标转换为GCJ02坐标
    if (mapType === 'amap') {
        const converted = wgs84ToGcj02(longitude, latitude);
        displayLng = converted[0];
        displayLat = converted[1];
        console.log(`[坐标转换] WGS84: ${longitude}, ${latitude} -> GCJ02: ${displayLng}, ${displayLat}`);
    }
    
    const center = ol.proj.fromLonLat([displayLng, displayLat]);
    currentMap.getView().setCenter(center);
    
    
    if (isInitialMarking) {
        currentMap.getView().setZoom(8);
    }
    
    
    const layers = currentMap.getLayers().getArray();
    const markerLayer = layers.find(layer => layer instanceof ol.layer.Vector);
    
    if (markerLayer) {
        const source = markerLayer.getSource();
        
        
        source.clear();
        
        
        const markerFeature = new ol.Feature({
            geometry: new ol.geom.Point(center),
            name: '基准站位置'
        });
        
        markerFeature.setStyle(new ol.style.Style({
            image: new ol.style.Icon({
                src: 'data:image/svg+xml;base64,' + btoa(`
                    <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
                        <defs>
                            <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
                                <feDropShadow dx="2" dy="2" stdDeviation="2" flood-color="rgba(0,0,0,0.3)"/>
                            </filter>
                        </defs>
                        <circle cx="16" cy="16" r="12" fill="transparent" stroke="rgba(21, 101, 192, 0.8)" stroke-width="3" filter="url(#shadow)"/>
                        <text x="16" y="21" text-anchor="middle" dominant-baseline="central" font-family="Arial, sans-serif" font-size="16" font-weight="bold" fill="#DC143C">T</text>
                    </svg>
                `),
                scale: 1,
                anchor: [0.5, 0.5]
            })
        }));
        
        source.addFeature(markerFeature);
        
        
        if (mountName) {
            const textFeature = new ol.Feature({
                geometry: new ol.geom.Point(center),
                name: '挂载点名称标签'
            });
            
            textFeature.setStyle(new ol.style.Style({
                text: new ol.style.Text({
                    text: mountName,
                    font: 'bold 18px Arial',
                    fill: new ol.style.Fill({
                        color: '#1565C0'
                    }),
                    stroke: new ol.style.Stroke({
                        color: '#FFFFFF',
                        width: 3
                    }),
                    offsetY: -25,
                    textAlign: 'center',
                    textBaseline: 'bottom'
                })
            }));
            
            source.addFeature(textFeature);
        }
        
        
        const circle20km = new ol.Feature({
            geometry: new ol.geom.Circle(center, 20000)
        });
        
        circle20km.setStyle(new ol.style.Style({
            fill: new ol.style.Fill({
                color: 'rgba(21, 101, 192, 0.15)'
            })
        }));
        
        source.addFeature(circle20km);
        
        
        const circle50km = new ol.Feature({
            geometry: new ol.geom.Circle(center, 50000)
        });
        
        circle50km.setStyle(new ol.style.Style({
            fill: new ol.style.Fill({
                color: 'rgba(66, 165, 245, 0.2)'
            })
        }));
        
        source.addFeature(circle50km);
    }
}

// SATELLITE
let satelliteContainers = {};
let satelliteData = {};
let frequencyMap = {};

// freq_map
async function loadFrequencyMap() {
    try {
        const response = await fetch('/static/freq_map.json');
        frequencyMap = await response.json();
        // console.log('频率映射表加载成功');
    } catch (error) {
        // console.error('频率映射表加载失败:', error);
    }
}




function getFrequencyInfo(constellation, channel) {
    
    const constellationMap = {
        'GPS': 'GPS',
        'GLONASS': 'GLO', 
        'GALILEO': 'GAL',
        'BDS': 'BDS',
        'QZSS': 'QZS',
        'SBAS': 'SBAS',
        'IRNSS': 'IRN',
        'NAVIC': 'NAV'
    };
    
    const mappedConstellation = constellationMap[constellation];
    if (!mappedConstellation || !frequencyMap[mappedConstellation] || !channel) {
        return { band: '未知', freq: '未知' };
    }
    
    const freqInfo = frequencyMap[mappedConstellation][channel];
    return freqInfo || { band: '未知', freq: '未知' };
}


function initializeSatelliteVisualization() {
    const satelliteContainer = document.getElementById('satellite-container');
    if (!satelliteContainer) {
        // console.warn('卫星容器不存在，无法初始化卫星可视化');
        return;
    }
    
    
    satelliteContainer.innerHTML = '';
    
    
    const supportedConstellations = ['GPS', 'GLONASS', 'GALILEO', 'BDS', 'QZSS', 'SBAS', 'IRNSS', 'NAVIC'];
    supportedConstellations.forEach(constellation => {
        createConstellationContainer(constellation);
        
        const constellationContainer = document.querySelector(`#chart-${constellation}`).closest('.constellation-container');
        if (constellationContainer) {
            constellationContainer.style.display = 'none';
        }
    });
    
    // console.log('卫星可视化初始化完成，已创建', supportedConstellations.length, '个星座容器（初始隐藏状态）');
}

 
function updateSatelliteVisualization(constellation, satellites) {
     
    satelliteData[constellation] = satellites;
    
    
    updateSatelliteStatus(satellites && satellites.length > 0);
    
     
    updateConstellationChart(constellation, satellites);
}


function createConstellationContainer(constellation) {
    const satelliteContainer = document.getElementById('satellite-container');
    
    const constellationDiv = document.createElement('div');
    constellationDiv.className = 'constellation-container';
    constellationDiv.id = `constellation-${constellation}`;
    
    constellationDiv.innerHTML = `
        <h5 class="constellation-title">${constellation}</h5>
        <div class="satellite-chart" id="chart-${constellation}"></div>
    `;
    
    satelliteContainer.appendChild(constellationDiv);
    satelliteContainers[constellation] = constellationDiv;
}


function updateConstellationChart(constellation, satellites) {
    const chartContainer = document.getElementById(`chart-${constellation}`);
    if (!chartContainer) {
        // console.warn(`图表容器 chart-${constellation} 不存在`);
        return;
    }
    
     
    const currentTime = Date.now();
    
    
    if (!satelliteData[constellation]) {
        satelliteData[constellation] = {};
    }
    
    
    satellites.forEach(satellite => {
        satelliteData[constellation][satellite.name] = {
            ...satellite,
            lastUpdate: currentTime
        };
    });
    
    
    const expireTime = 10000; // 10秒
    Object.keys(satelliteData[constellation]).forEach(satName => {
        if (currentTime - satelliteData[constellation][satName].lastUpdate > expireTime) {
            delete satelliteData[constellation][satName];
        }
    });
    
    
    const constellationContainer = chartContainer.closest('.constellation-container');
    const activeSatelliteCount = Object.keys(satelliteData[constellation]).length;
    
    
    if (activeSatelliteCount === 0) {
        
        if (constellationContainer) {
            constellationContainer.style.display = 'none';
        }
        // console.log(`${constellation} 星座模块已隐藏（无数据）`);
        return;
    } else {
        
        if (constellationContainer) {
            constellationContainer.style.display = 'block';
        }
    }
    
    
    chartContainer.innerHTML = '';
    
    const activeSatellites = Object.values(satelliteData[constellation]);
    const satelliteCount = activeSatellites.length;
    
    
    const containerWidth = chartContainer.offsetWidth || 300; 
    const minBarWidth = 20; 
    const maxBarWidth = 60; 
    const spacing = 5; 
    
    let barWidth = Math.floor((containerWidth - (satelliteCount - 1) * spacing) / satelliteCount);
    barWidth = Math.max(minBarWidth, Math.min(maxBarWidth, barWidth));
    
    activeSatellites.forEach(satellite => {
        const barContainer = document.createElement('div');
        barContainer.className = 'satellite-bar-container';
        barContainer.style.width = `${barWidth}px`;
        barContainer.style.marginRight = `${spacing}px`;
        barContainer.style.display = 'inline-block';
        barContainer.style.verticalAlign = 'bottom';
        
        const bar = document.createElement('div');
        bar.className = 'satellite-bar';
        bar.style.height = `${Math.max(satellite.signalStrength * 2, 10)}px`;
        bar.style.backgroundColor = getSignalColor(satellite.signalStrength);
        bar.style.width = '100%';
        
        const label = document.createElement('div');
        label.className = 'satellite-label';
        label.textContent = satellite.name;
        label.style.fontSize = barWidth < 30 ? '10px' : '12px'; 
        label.style.textAlign = 'center';
        
        const strength = document.createElement('div');
        strength.className = 'satellite-strength';
        strength.textContent = satellite.signalStrength;
        strength.style.fontSize = barWidth < 30 ? '9px' : '11px';
        strength.style.textAlign = 'center';
        
        
        barContainer.addEventListener('mouseenter', (e) => {
            showSatelliteTooltip(e, satellite, constellation);
        });
        
        barContainer.addEventListener('mouseleave', () => {
            
            tooltipHideTimeout = setTimeout(() => {
                hideSatelliteTooltip();
            }, 300);
        });
        
        
        barContainer.addEventListener('mousemove', (e) => {
            updateTooltipPosition(e);
        });
        
        barContainer.appendChild(strength);
        barContainer.appendChild(bar);
        barContainer.appendChild(label);
        chartContainer.appendChild(barContainer);
    });
    
    
    const lastBar = chartContainer.lastElementChild;
    if (lastBar) {
        lastBar.style.marginRight = '0';
    }
    
    
}


function getSignalColor(strength) {
    if (strength >= 40) return '#4CAF50'; // 绿色
    if (strength >= 30) return '#FFC107'; // 黄色
    if (strength >= 20) return '#FF9800'; // 橙色
    return '#F44336'; // 红色
}

let currentTooltip = null;
let tooltipHideTimeout = null;


function showSatelliteTooltip(event, satellite, constellation) {
    
    if (tooltipHideTimeout) {
        clearTimeout(tooltipHideTimeout);
        tooltipHideTimeout = null;
    }
    
    
    hideSatelliteTooltip();
    
    
    const freqInfo = getFrequencyInfo(constellation, satellite.channel);
    
    const tooltip = document.createElement('div');
    tooltip.className = 'satellite-tooltip';
    tooltip.innerHTML = `
        <div><strong>${satellite.name}</strong></div>
        <div>信号强度：${satellite.signalStrength} dBHz</div>
                    <div>高度角：${satellite.elevation}°</div>
                    <div>方位角：${satellite.azimuth}°</div>
                    <div>频段：${freqInfo.band}</div>
                    <div>频率：${freqInfo.freq}</div>
                    <div>信道：${satellite.channel || '未知'}</div>
    `;
    
    tooltip.style.cssText = `
        position: absolute;
        background: rgba(0, 0, 0, 0.9);
        color: white;
        padding: 10px;
        border-radius: 5px;
        font-size: 12px;
        z-index: 10000;
        pointer-events: none;
        box-shadow: 0 2px 10px rgba(0,0,0,0.3);
        max-width: 200px;
        transition: opacity 0.2s ease;
    `;
    
    document.body.appendChild(tooltip);
    currentTooltip = tooltip;
    
    
    updateTooltipPosition(event);
}


function updateTooltipPosition(event) {
    if (!currentTooltip) return;
    
    const tooltip = currentTooltip;
    
    
    let left = event.pageX + 10;
    let top = event.pageY - 10;
    
    
    if (left + tooltip.offsetWidth > window.innerWidth + window.scrollX) {
        left = event.pageX - tooltip.offsetWidth - 10;
    }
    
    
    if (top < window.scrollY) {
        top = event.pageY + 20;
    }
    
    
    if (top + tooltip.offsetHeight > window.innerHeight + window.scrollY) {
        top = event.pageY - tooltip.offsetHeight - 10;
    }
    
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
}


function hideSatelliteTooltip() {
    if (currentTooltip) {
        currentTooltip.remove();
        currentTooltip = null;
    }
    if (tooltipHideTimeout) {
        clearTimeout(tooltipHideTimeout);
        tooltipHideTimeout = null;
    }
}


function getDashboardContent() {
    return `
        <div class="page-header">
            <h3>系统状态</h3>
            <div class="dashboard-timestamp" id="dashboard-timestamp">加载中...</div>
        </div>
        
        <!-- 系统概览卡片 -->
        <div class="dashboard-cards">
            <div class="dashboard-card">
                <div class="card-icon">⏰</div>
                <div class="card-content">
                    <div class="card-title">运行时间</div>
                    <div class="card-value" id="system-uptime">-</div>
                </div>
            </div>
            
            <div class="dashboard-card">
                <div class="card-icon">⚡</div>
                <div class="card-content">
                    <div class="card-title">CPU 使用率</div>
                    <div class="card-value" id="system-cpu">-</div>
                </div>
            </div>
            
            <div class="dashboard-card">
                <div class="card-icon">📈</div>
                <div class="card-content">
                    <div class="card-title">内存使用率</div>
                    <div class="card-value" id="system-memory">-</div>
                    <div class="card-detail" id="system-memory-detail">-</div>
                </div>
            </div>
            
            <div class="dashboard-card">
                <div class="card-icon">📻</div>
                <div class="card-content">
                    <div class="card-title">网络带宽</div>
                    <div class="card-value" id="system-bandwidth">-</div>
                </div>
            </div>
        </div>
        
        <!-- 连接统计 -->
        <div class="dashboard-section">
            <h4>连接统计</h4>
            <div class="stats-grid">
                <div class="stat-item">
                    <span class="stat-label">当前连接数：</span>
                    <span class="stat-value" id="active-connections">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">最大连接数：</span>
                    <span class="stat-value" id="max-connections">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">总连接数：</span>
                    <span class="stat-value" id="total-connections">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">拒绝连接数：</span>
                    <span class="stat-value" id="rejected-connections">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">在线挂载点：</span>
                    <span class="stat-value" id="total-mounts">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">移动站连接数：</span>
                    <span class="stat-value" id="total-users">-</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">数据传输量：</span>
                    <span class="stat-value" id="total-data">-</span>
                </div>
            </div>
        </div>
        
        <!-- 挂载点详情 -->
        <div class="dashboard-section">
            <h4>挂载点详情</h4>
            <div class="mounts-container" id="mounts-detail">
                <div class="loading-text">加载中...</div>
            </div>
        </div>
        
        <style>
        .dashboard-cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        
        .dashboard-card {
            background: linear-gradient(135deg, rgba(255, 255, 255, 0.95), rgba(255, 255, 255, 0.85));
            backdrop-filter: blur(15px);
            border-radius: 15px;
            padding: 1.2rem;
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.08), 0 2px 6px rgba(0, 0, 0, 0.04);
            border: 1px solid rgba(255, 255, 255, 0.3);
            display: flex;
            align-items: center;
            gap: 1rem;
            transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            position: relative;
            overflow: hidden;
            animation: fadeInUp 0.6s ease-out;
        }
        
        .dashboard-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.4), transparent);
            transition: left 0.6s ease;
        }
        
        .dashboard-card:hover::before {
            left: 100%;
        }
        
        .dashboard-card:hover {
            transform: translateY(-8px) scale(1.02);
            box-shadow: 0 15px 50px rgba(0, 0, 0, 0.15), 0 5px 20px rgba(0, 0, 0, 0.1);
        }
        
        .card-icon {
            font-size: 1.8em;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            animation: pulse 2s ease-in-out infinite;
        }
        
        .card-content {
            flex: 1;
            position: relative;
            z-index: 1;
        }
        
        .card-title {
            font-size: 0.8rem;
            color: #555;
            margin-bottom: 0.5rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 600;
        }
        
        .card-value {
            font-size: 1.4rem;
            font-weight: 700;
            background: linear-gradient(135deg, #333, #555);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            transition: all 0.3s ease;
        }
        
        .dashboard-card:hover .card-value {
            transform: scale(1.1);
        }
        
        .card-detail {
            font-size: 0.8em;
            color: #888;
            margin-top: 2px;
        }
        
        .dashboard-section {
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        .dashboard-section h4 {
            margin: 0 0 15px 0;
            color: #333;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        
        .stat-item {
            display: flex;
            justify-content: space-between;
            padding: 10px;
            background: #f8f9fa;
            border-radius: 4px;
        }
        
        .stat-label {
            color: #666;
        }
        
        .stat-value {
            font-weight: bold;
            color: #333;
        }
        
        .mounts-container {
            max-height: 400px;
            overflow-y: auto;
        }
        
        .mount-item {
            background: #f8f9fa;
            border-radius: 4px;
            padding: 15px;
            margin-bottom: 10px;
            border-left: 4px solid #007bff;
        }
        
        .mount-name {
            font-weight: bold;
            color: #333;
            margin-bottom: 5px;
        }
        
        .mount-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 10px;
            font-size: 0.9em;
            color: #666;
        }
        
        .dashboard-timestamp {
            color: #666;
            font-size: 0.9em;
        }
        
        .loading-text {
            text-align: center;
            color: #666;
            padding: 20px;
        }
        </style>
    `;
}

// user
function getUsersContent(connections) {
    // Extract unique mount names and usernames for filter dropdowns
    const allMounts = [...new Set((connections || []).map(c => c.mount_name).filter(Boolean))].sort();
    const allUsernames = [...new Set((connections || []).map(c => c.username).filter(Boolean))].sort();
    
    const mountOptions = allMounts.map(m => `<option value="${m}">${m}</option>`).join('');
    const usernameOptions = allUsernames.map(u => `<option value="${u}">${u}</option>`).join('');
    const connectionCount = connections ? connections.length : 0;
    
    const rowsHtml = (connections || []).map(conn => {
        const ggaQuality = conn.gga_quality;
        const ggaInfo = ggaQuality !== null && ggaQuality !== undefined ? 
            (GGA_QUALITY_MAP[ggaQuality] || { label: ggaQuality + '-未知', color: '#6c757d' }) :
            { label: '-', color: '#6c757d' };
        const dataRate = formatDataRate(conn.data_rate || 0);
        const bytesSent = formatBytes(conn.bytes_sent || 0);
        return `
            <tr data-connection-id="${conn.connection_id}" data-username="${conn.username}">
                <td>${conn.connection_id}</td>
                <td>${conn.username}</td>
                <td>${conn.mount_name}</td>
                <td>${conn.ip_address}</td>
                <td>${conn.connect_time}</td>
                <td>${bytesSent}</td>
                <td>${dataRate}</td>
                <td class="diff-status-cell" style="color: ${ggaInfo.color}; font-weight: 600;">${ggaInfo.label}</td>
            </tr>
        `;
    }).join('');
    
    setTimeout(() => {
        initSearchableDropdown('mount-dropdown', 'mobile-station-mount-filter', 'mobile-station-mount-filter-input', 'mount-dropdown-list', function(value) {
            window.mobileStationMountFilter = value;
            loadPageContent('users');
        });
        initSearchableDropdown('username-dropdown', 'mobile-station-username-filter', 'mobile-station-username-filter-input', 'username-dropdown-list', function(value) {
            window.mobileStationUsernameFilter = value;
            loadPageContent('users');
        });
    }, 0);
    
    return `
        <div class="page-header">
            <h3>移动站管理</h3>
            <div style="display: flex; gap: 10px;">
                <button onclick="showAddUserForm()" class="btn btn-primary">+ 添加用户</button>
                <button onclick="loadPageContent('users')" class="btn btn-secondary" title="刷新数据">🔄 刷新</button>
            </div>
        </div>
        <div class="anonymous-access-bar" style="background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 12px 15px; margin-bottom: 15px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
            <input type="checkbox" id="anonymous-access" onchange="toggleAnonymousAccess()" style="width: 18px; height: 18px; cursor: pointer; margin: 0;">
            <label for="anonymous-access" style="margin: 0; cursor: pointer; font-weight: 500;">
                允许匿名访问（连接 NTRIP 无需用户名/密码）
            </label>
            <span id="anonymous-status" style="font-size: 0.85em; color: #6c757d;"></span>
        </div>
        <div class="filter-bar" style="background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 14px 18px; margin-bottom: 15px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap;">
            <div style="display: flex; align-items: center; gap: 8px;">
                <label style="margin: 0; font-weight: 600; color: #495057; white-space: nowrap; font-size: 0.9em;">挂载点</label>
                <div class="searchable-dropdown" style="position: relative;" id="mount-dropdown">
                    <input type="text" id="mobile-station-mount-filter-input" placeholder="搜索挂载点..." autocomplete="off"
                        style="min-width: 160px; border-radius: 6px; border: 1px solid #ced4da; padding: 6px 10px; font-size: 0.9em; background: white;"
                        value="${window.mobileStationMountFilter || ''}">
                    <div id="mount-dropdown-list" class="dropdown-list" style="display: none; position: absolute; top: 100%; left: 0; right: 0; background: white; border: 1px solid #ced4da; border-radius: 6px; max-height: 200px; overflow-y: auto; z-index: 1000; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
                        <div class="dropdown-item" data-value="" style="padding: 8px 12px; cursor: pointer; font-size: 0.9em; border-bottom: 1px solid #f0f0f0;">全部挂载点</div>
                        ${mountOptions.replace(/<option value="([^"]+)">([^<]+)<\/option>/g, '<div class="dropdown-item" data-value="$1" style="padding: 8px 12px; cursor: pointer; font-size: 0.9em; border-bottom: 1px solid #f0f0f0;">$2</div>')}
                    </div>
                    <input type="hidden" id="mobile-station-mount-filter" value="${window.mobileStationMountFilter || ''}">
                </div>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
                <label style="margin: 0; font-weight: 600; color: #495057; white-space: nowrap; font-size: 0.9em;">用户名</label>
                <div class="searchable-dropdown" style="position: relative;" id="username-dropdown">
                    <input type="text" id="mobile-station-username-filter-input" placeholder="搜索用户名..." autocomplete="off"
                        style="min-width: 160px; border-radius: 6px; border: 1px solid #ced4da; padding: 6px 10px; font-size: 0.9em; background: white;"
                        value="${window.mobileStationUsernameFilter || ''}">
                    <div id="username-dropdown-list" class="dropdown-list" style="display: none; position: absolute; top: 100%; left: 0; right: 0; background: white; border: 1px solid #ced4da; border-radius: 6px; max-height: 200px; overflow-y: auto; z-index: 1000; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
                        <div class="dropdown-item" data-value="" style="padding: 8px 12px; cursor: pointer; font-size: 0.9em; border-bottom: 1px solid #f0f0f0;">全部用户</div>
                        ${usernameOptions.replace(/<option value="([^"]+)">([^<]+)<\/option>/g, '<div class="dropdown-item" data-value="$1" style="padding: 8px 12px; cursor: pointer; font-size: 0.9em; border-bottom: 1px solid #f0f0f0;">$2</div>')}
                    </div>
                    <input type="hidden" id="mobile-station-username-filter" value="${window.mobileStationUsernameFilter || ''}">
                </div>
            </div>
            <span id="mobile-station-filter-status" style="font-size: 0.85em; color: #6c757d; white-space: nowrap; background: rgba(255,255,255,0.7); padding: 4px 10px; border-radius: 4px;">
                共 ${connectionCount} 个连接
            </span>
        </div>
        <div class="table-container">
            <table class="data-table" id="mobile-station-table">
                <thead>
                    <tr>
                        <th>连接 ID</th>
                        <th>用户名</th>
                        <th>挂载点</th>
                        <th>IP 地址</th>
                        <th>接入时间</th>
                        <th>已发送数据</th>
                        <th>发送速率</th>
                        <th>差分状态</th>
                    </tr>
                </thead>
                <tbody>
                    ${rowsHtml || '<tr><td colspan="8" style="text-align: center; color: #6c757d; padding: 2rem;">暂无移动站连接</td></tr>'}
                </tbody>
            </table>
        </div>
    `;
}

// 加载用户连接详情
async function loadUserConnections(username, container) {
    container.innerHTML = '<p class="loading-text" style="margin: 0;">正在加载连接详情...</p>';
    
    try {
        const mountFilter = window.userMountFilter || '';
        const url = mountFilter 
            ? `/api/users/${encodeURIComponent(username)}/connections?mount_name=${encodeURIComponent(mountFilter)}`
            : `/api/users/${encodeURIComponent(username)}/connections`;
        
        const response = await fetch(url);
        const result = await handleApiResponse(response);
        
        if (!result.success || !result.connections) {
            container.innerHTML = '<p class="empty-text" style="margin: 0; color: #6c757d;">暂无连接详情</p>';
            return;
        }
        
        const connections = result.connections;
        if (connections.length === 0) {
            container.innerHTML = '<p class="empty-text" style="margin: 0; color: #6c757d;">暂无连接</p>';
            return;
        }
        
        const diffingCount = connections.filter(c => c.is_diffing).length;
        const summaryHtml = `<div style="margin-bottom: 10px; font-size: 0.9em; color: #6c757d;">
            共 ${connections.length} 个连接，<span style="color: #28a745;">${diffingCount} 个差分中</span>
        </div>`;
        
        const rows = connections.map(conn => {
            const ggaQuality = conn.gga_quality;
            const ggaInfo = ggaQuality !== null && ggaQuality !== undefined ? 
                (GGA_QUALITY_MAP[ggaQuality] || { label: ggaQuality + '-未知', color: '#6c757d' }) :
                { label: '-', color: '#6c757d' };
            const dataRate = formatDataRate(conn.data_rate || 0);
            const bytesSent = formatBytes(conn.bytes_sent || 0);
            return `
                <tr>
                    <td>${conn.mount_name || '-'}</td>
                    <td>${conn.ip_address || '-'}</td>
                    <td>${conn.connect_time || '-'}</td>
                    <td>${bytesSent}</td>
                    <td>${dataRate}</td>
                    <td style="color: ${ggaInfo.color}; font-weight: 600;">${ggaInfo.label}</td>
                </tr>
            `;
        }).join('');
        
        container.innerHTML = `
            ${summaryHtml}
            <div class="table-container" style="max-height: 300px; overflow-y: auto;">
                <table class="data-table" style="min-width: 600px; background: white;">
                    <thead>
                        <tr>
                            <th>挂载点</th>
                            <th>IP 地址</th>
                            <th>接入时间</th>
                            <th>已发送数据</th>
                            <th>发送速率</th>
                            <th>差分状态</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rows}
                    </tbody>
                </table>
            </div>
        `;
    } catch (error) {
        container.innerHTML = `<p class="empty-text" style="margin: 0; color: #dc3545;">加载失败：${error.message}</p>`;
    }
}

// 格式化数据速率
function formatDataRate(bytesPerSecond) {
    if (bytesPerSecond <= 0) return '0 B/s';
    if (bytesPerSecond < 1024) return `${bytesPerSecond.toFixed(1)} B/s`;
    if (bytesPerSecond < 1024 * 1024) return `${(bytesPerSecond / 1024).toFixed(2)} KB/s`;
    return `${(bytesPerSecond / (1024 * 1024)).toFixed(2)} MB/s`;
}

// 挂载点管理内容
function getMountsContent(mounts) {
    let mountsHtml = mounts.map(mount => {
        // 优先使用从API获取的在线状态，如果没有则使用WebSocket数据
        const isOnline = mount.active !== undefined ? mount.active : (window.onlineMounts && (mount.mount in window.onlineMounts));
        const statusHtml = isOnline ? 
            '<span style="color: #28a745; font-weight: bold;">● 在线</span>' : 
            '<span style="color: #6c757d;">○ 离线</span>';
        const isAnonymous = mount.anonymous === true || mount.id === -1;
        const actionButtons = isAnonymous ?
            '<span style="color: #6c757d; font-size: 0.85em;">匿名</span>' :
            `
                <button onclick="editMount('${mount.mount}')" class="btn btn-primary btn-sm">编辑</button>
                <button onclick="deleteMount('${mount.mount}')" class="btn btn-danger btn-sm">删除</button>
            `;
        return `
            <tr class="mount-row" data-mount="${mount.mount}">
                <td>${mount.mount}</td>
                <td class="mount-status">${statusHtml}</td>
                <td class="mount-connections">${mount.connections || 0}</td>
                <td>${isAnonymous ? '匿名' : (mount.username || '未指定')}</td>
                <td>${isAnonymous ? '系统自动注册' : (mount.description || '-')}</td>
                <td>
                    ${actionButtons}
                </td>
            </tr>
        `;
    }).join('');
    
    return `
        <div class="page-header">
            <h3>挂载点管理</h3>
            <button onclick="showAddMountForm()" class="btn btn-primary">添加挂载点</button>
        </div>
        <div class="anonymous-access-bar" style="background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 12px 15px; margin-bottom: 15px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
            <input type="checkbox" id="anonymous-access" onchange="toggleAnonymousAccess()" style="width: 18px; height: 18px; cursor: pointer; margin: 0;">
            <label for="anonymous-access" style="margin: 0; cursor: pointer; font-weight: 500;">
                允许匿名访问（连接 NTRIP 无需用户名/密码）
            </label>
            <span id="anonymous-status" style="font-size: 0.85em; color: #6c757d;"></span>
        </div>
        <div class="table-container">
            <table class="data-table" id="mobile-station-table">
                <thead>
                    <tr>
                        <th>挂载点</th>
                        <th>状态</th>
                        <th>连接数</th>
                        <th>所属用户</th>
                        <th>描述</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody>
                    ${mountsHtml}
                </tbody>
            </table>
        </div>
    `;
}

// RTCM监控内容
function getMonitorContent() {
    return `
        <div class="page-header">
            <h3><i class="fas fa-satellite-dish"></i> 基准站 STR 信息</h3>
            <p class="page-subtitle">NTRIP 数据流与基准站状态实时监控</p>
        </div>
        
        <div class="monitor-dashboard">
            <!-- 主要内容区域 -->
            <div class="monitor-grid">
                <!-- STR数据表 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-table"></i> STR 数据表</h4>
                    </div>
                    <div class="card-content" id="str-data">
                        <p class="loading-text"><i class="fas fa-spinner fa-spin"></i> 正在加载 STR 表数据...</p>
                    </div>
                </div>

                <!-- 基准站信息 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-broadcast-tower"></i> 基准站信息</h4>
                        <div class="card-status" id="station-status">
                            <span class="status-dot waiting"></span>
                            <span>等待选择</span>
                        </div>
                    </div>
                    <div class="card-content">
                        <div id="station-info" class="station-info-container">
                            <div class="empty-state">
                                <i class="fas fa-mouse-pointer"></i>
                                <p>请点击 STR 表中的 INFO 按钮选择挂载点</p>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 基准站位置 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-map-marker-alt"></i> 基准站位置</h4>
                    </div>
                    <div class="card-content map-content">
                        <div id="map-container" class="map-container">
                            <div id="map" class="map-display"></div>
                            <div class="map-overlay" id="map-loading">
                                <i class="fas fa-map"></i>
                                <p>等待位置数据...</p>
                            </div>
                            <div id="map-switch" class="map-switch-floating">
                                <button id="amap-btn" class="btn btn-sm btn-primary">高德地图</button>
                                <button id="osm-btn" class="btn btn-sm btn-secondary">OpenStreetMap</button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 卫星数据可视化 - 全宽 -->
                <div class="monitor-card full-width">
                    <div class="card-header">
                        <h4><i class="fas fa-satellite"></i> 卫星数据可视化</h4>
                        <div class="card-status" id="satellite-status">
                            <span class="status-dot waiting"></span>
                            <span>等待数据</span>
                        </div>
                    </div>
                    <div class="card-content">
                        <div id="satellite-container" class="satellite-container">
                            <div class="empty-state">
                                <i class="fas fa-satellite-dish"></i>
                                <p>等待卫星数据...</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
}


// ==================== 数据查看（移动站 GGA 数据） ====================

// 排序状态（每次进入页面由 getDataViewContent 重置为默认）
let mobileDataSort = { by: 'event_time', order: 'DESC' };
// 每页条数（每次进入页面由 getDataViewContent 重置为 100）
let mobileDataPageSize = 100;
// 各字段的默认排序方向：时间/数字默认 DESC，文本字段默认 ASC
const MOBILE_DATA_DEFAULT_ORDER = {
    event_time: 'DESC',
    username: 'ASC',
    mount_name: 'ASC',
};
// 每页条数可选值
const MOBILE_DATA_PAGE_SIZE_OPTIONS = [50, 100, 200, 500];

function getDataViewContent() {
    // 每次进入页面，重置排序状态和每页条数
    mobileDataSort = { by: 'event_time', order: 'DESC' };
    mobileDataPageSize = 100;
    const pageSizeOptions = MOBILE_DATA_PAGE_SIZE_OPTIONS
        .map(n => `<option value="${n}" ${n === 100 ? 'selected' : ''}>${n} 条/页</option>`)
        .join('');
    // 差分状态下拉：value 与后端 _build_quality_clause 协议一致
    //   '' = 全部；'rtk' = 2/4/5 聚合；'fixed'/'float'/'invalid'/'null' = 单值；'0'..'8' = 精确数值
    const qualityOptions = [
        { v: '', l: '全部' },
        { v: 'rtk', l: '差分中 (DGPS/RTK)' },
        { v: 'fixed', l: 'RTK 固定 (4)' },
        { v: 'float', l: 'RTK 浮点 (5)' },
        { v: 'invalid', l: '无效 (0)' },
        { v: 'null', l: '未知 (未解析)' },
    ];
    const qualitySelect = qualityOptions
        .map(o => `<option value="${o.v}">${o.l}</option>`)
        .join('');
    return `
        <div class="page-header">
            <h3>数据查看</h3>
            <p class="page-subtitle">移动站发送给 Caster 的 GGA 定位数据（按条数上限保留最新 <span id="mobile-data-max-records">--</span> 条）</p>
        </div>
        <div class="settings-container" style="max-width: 100%; margin: 0 auto;">
            <div class="settings-section">
                <div class="form-group" style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 0;">
                    <input type="text" id="mobile-data-user" placeholder="用户名" class="form-control" style="width: auto; min-width: 140px;">
                    <input type="text" id="mobile-data-mount" placeholder="挂载点名称" class="form-control" style="width: auto; min-width: 140px;">
                    <select id="mobile-data-quality" class="form-control" style="width: auto; min-width: 140px;" title="差分状态筛选">
                        ${qualitySelect}
                    </select>
                    <input type="datetime-local" id="mobile-data-start" class="form-control" style="width: auto; min-width: 160px;">
                    <input type="datetime-local" id="mobile-data-end" class="form-control" style="width: auto; min-width: 160px;">
                    <button onclick="loadMobileData(0, mobileDataPageSize)" class="btn btn-primary">查询</button>
                    <button onclick="resetMobileDataFilter()" class="btn btn-secondary">重置</button>
                    <select id="mobile-data-page-size" class="form-control" style="width: auto; min-width: 110px;" onchange="changeMobileDataPageSize(this.value)">
                        ${pageSizeOptions}
                    </select>
                </div>
            </div>
            <div id="mobile-data-list">
                <p class="loading-text">正在加载移动站数据...</p>
            </div>
        </div>
    `;
}

function changeMobileDataPageSize(newSize) {
    const n = parseInt(newSize, 10);
    if (!Number.isFinite(n) || n <= 0) return;
    mobileDataPageSize = n;
    loadMobileData(0, n);
}

function resetMobileDataFilter() {
    const userEl = document.getElementById('mobile-data-user');
    const mountEl = document.getElementById('mobile-data-mount');
    const qualityEl = document.getElementById('mobile-data-quality');
    const startEl = document.getElementById('mobile-data-start');
    const endEl = document.getElementById('mobile-data-end');
    const pageSizeEl = document.getElementById('mobile-data-page-size');
    if (userEl) userEl.value = '';
    if (mountEl) mountEl.value = '';
    if (qualityEl) qualityEl.value = '';
    if (startEl) startEl.value = '';
    if (endEl) endEl.value = '';
    if (pageSizeEl) pageSizeEl.value = '100';
    mobileDataPageSize = 100;
    loadMobileData(0, 100);
}

async function loadMobileData(offset = 0, limit = 100) {
    try {
        const username = document.getElementById('mobile-data-user')?.value.trim() || '';
        const mountName = document.getElementById('mobile-data-mount')?.value.trim() || '';
        const quality = document.getElementById('mobile-data-quality')?.value || '';
        const startInput = document.getElementById('mobile-data-start')?.value || '';
        const endInput = document.getElementById('mobile-data-end')?.value || '';
        const startTime = startInput ? startInput.replace('T', ' ') + ':00' : '';
        const endTime = endInput ? endInput.replace('T', ' ') + ':00' : '';

        const params = new URLSearchParams();
        if (username) params.append('username', username);
        if (mountName) params.append('mount_name', mountName);
        if (quality) params.append('gga_quality', quality);
        if (startTime) params.append('start_time', startTime);
        if (endTime) params.append('end_time', endTime);
        params.append('limit', String(limit));
        params.append('offset', String(offset));
        params.append('sort_by', mobileDataSort.by);
        params.append('sort_order', mobileDataSort.order);

        const response = await fetch('/api/mobile_data?' + params.toString());
        const result = await handleApiResponse(response);
        if (result.success) {
            const maxEl = document.getElementById('mobile-data-max-records');
            if (maxEl && typeof result.max_records !== 'undefined') {
                maxEl.textContent = result.max_records.toLocaleString();
            }
            // 同步后端确认后的 sort
            if (result.sort_by) mobileDataSort.by = result.sort_by;
            if (result.sort_order) mobileDataSort.order = result.sort_order;
            renderMobileData(result.data || [], {
                offset: result.offset,
                limit: result.limit,
                total: result.total
            });
        } else {
            showAlert('加载移动站数据失败：' + (result.error || '未知错误'), 'error');
        }
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('加载移动站数据失败：' + error.message, 'error');
        }
    }
}

// 点击列头：相同列切换方向，不同列使用该字段默认方向
function sortMobileData(column) {
    if (mobileDataSort.by === column) {
        mobileDataSort.order = mobileDataSort.order === 'ASC' ? 'DESC' : 'ASC';
    } else {
        mobileDataSort.by = column;
        mobileDataSort.order = MOBILE_DATA_DEFAULT_ORDER[column] || 'ASC';
    }
    loadMobileData(0, 100);
}

function renderMobileData(rows, pagination = null) {
    const container = document.getElementById('mobile-data-list');
    if (!container) return;

    let paginationHtml = '';
    if (pagination) {
        const { offset, limit, total } = pagination;
        const currentPage = Math.floor(offset / limit) + 1;
        const totalPages = Math.max(1, Math.ceil(total / limit));
        const hasPrev = offset > 0;
        const hasNext = offset + rows.length < total;
        const firstOffset = 0;
        const lastOffset = Math.max(0, (totalPages - 1) * limit);
        const startRange = total > 0 ? offset + 1 : 0;
        const endRange = offset + rows.length;

        paginationHtml = `
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 10px; font-size: 0.85em; color: #6c757d;">
                <div>显示第 ${startRange} - ${endRange} 条，共 ${total} 条</div>
                <div style="display: flex; gap: 5px; align-items: center; flex-wrap: wrap;">
                    <button class="btn btn-secondary btn-sm" ${!hasPrev ? 'disabled' : ''} onclick="loadMobileData(${firstOffset}, ${limit})" title="首页">« 首页</button>
                    <button class="btn btn-secondary btn-sm" ${!hasPrev ? 'disabled' : ''} onclick="loadMobileData(${offset - limit}, ${limit})" title="上一页">上一页</button>
                    <span style="display: inline-flex; align-items: center; gap: 4px;">
                        第
                        <input type="number" id="mobile-data-page-input" min="1" max="${totalPages}" value="${currentPage}"
                               style="width: 60px; padding: 2px 6px; font-size: 0.95em; text-align: center; border: 1px solid #ced4da; border-radius: 3px;"
                               onkeydown="if(event.key === 'Enter'){ event.preventDefault(); jumpMobileDataPage(${limit}); }" />
                        / ${totalPages} 页
                    </span>
                    <button class="btn btn-primary btn-sm" onclick="jumpMobileDataPage(${limit})" title="跳转到指定页">跳转</button>
                    <button class="btn btn-secondary btn-sm" ${!hasNext ? 'disabled' : ''} onclick="loadMobileData(${offset + limit}, ${limit})" title="下一页">下一页</button>
                    <button class="btn btn-secondary btn-sm" ${!hasNext ? 'disabled' : ''} onclick="loadMobileData(${lastOffset}, ${limit})" title="尾页">尾页 »</button>
                </div>
            </div>
        `;
    }

    if (!rows || rows.length === 0) {
        container.innerHTML = `
            <p class="empty-text" style="color: #6c757d; padding: 10px 0;">暂无移动站 GGA 数据。移动站连接后会自动以 1Hz 发送 GGA 报文并保存。</p>
            ${paginationHtml}
        `;
        return;
    }

    const tableRows = rows.map(r => {
        const time = r.event_time || '-';
        const user = r.username ? escapeHtml(r.username) : '-';
        const mount = r.mount_name ? escapeHtml(r.mount_name) : '-';
        const nmeaType = r.nmea_type ? escapeHtml(r.nmea_type) : '-';
        const rawData = r.raw_data ? escapeHtml(r.raw_data) : '-';
        // 差分状态：复用前端的 GGA_QUALITY_MAP，未知/null 单独显示
        let qualityCell;
        if (r.gga_quality === null || r.gga_quality === undefined) {
            qualityCell = `<span style="color: #adb5bd;">未解析</span>`;
        } else {
            const info = GGA_QUALITY_MAP[r.gga_quality] || { label: r.gga_quality + '-未知', color: '#6c757d' };
            qualityCell = `<span style="color: ${info.color}; font-weight: 600;">${info.label}</span>`;
        }
        return `
            <tr>
                <td style="white-space: nowrap;">${escapeHtml(time)}</td>
                <td>${user}</td>
                <td>${mount}</td>
                <td><code>${nmeaType}</code></td>
                <td>${qualityCell}</td>
                <td style="font-family: monospace; font-size: 0.85em; word-break: break-all;">${rawData}</td>
            </tr>
        `;
    }).join('');

    container.innerHTML = `
        <div class="table-container" style="max-height: 800px; overflow-y: auto;">
            <table class="data-table" style="min-width: 750px;">
                <thead style="position: sticky; top: 0; z-index: 1; background: #f8f9fa;">
                    <tr>
                        <th style="white-space: nowrap; cursor: pointer; user-select: none;" onclick="sortMobileData('event_time')" title="点击按时间排序">时间 ${sortIndicator('event_time')}</th>
                        <th style="cursor: pointer; user-select: none;" onclick="sortMobileData('username')" title="点击按用户名排序">用户名 ${sortIndicator('username')}</th>
                        <th style="cursor: pointer; user-select: none;" onclick="sortMobileData('mount_name')" title="点击按挂载点排序">挂载点 ${sortIndicator('mount_name')}</th>
                        <th>NMEA 类型</th>
                        <th>差分状态</th>
                        <th>原始数据</th>
                    </tr>
                </thead>
                <tbody>
                    ${tableRows}
                </tbody>
            </table>
        </div>
        ${paginationHtml}
    `;
}

// 生成排序方向指示器
function sortIndicator(column) {
    if (mobileDataSort.by !== column) return '<span style="color: #adb5bd; font-size: 0.85em;">⇅</span>';
    const arrow = mobileDataSort.order === 'ASC' ? '↑' : '↓';
    return `<span style="color: #007bff; font-weight: bold;">${arrow}</span>`;
}

function jumpMobileDataPage(limit = 100) {
    const input = document.getElementById('mobile-data-page-input');
    if (!input) return;
    let page = parseInt(input.value, 10);
    if (!Number.isFinite(page) || page < 1) {
        page = 1;
        input.value = 1;
    }
    // 读取当前实际的总页数，从 input 的 max 属性取
    const maxPage = parseInt(input.max, 10);
    if (Number.isFinite(maxPage) && maxPage > 0 && page > maxPage) {
        page = maxPage;
        input.value = maxPage;
    }
    const offset = (page - 1) * limit;
    loadMobileData(offset, limit);
}

// settings
function getSettingsContent() {
    return `
        <div class="page-header">
            <h3>系统设置</h3>
        </div>
        <div class="settings-container">
            <div class="settings-section">
                <h4>安全设置</h4>
                <div class="form-group">
                    <label for="admin-password">新密码：</label>
                    <input type="password" id="admin-password" placeholder="请输入新密码" class="form-control">
                </div>
                <div class="form-group">
                    <label for="confirm-password">确认密码：</label>
                    <input type="password" id="confirm-password" placeholder="请再次输入密码" class="form-control">
                </div>
                <button onclick="changePassword()" class="btn btn-primary">修改管理员密码</button>
            </div>

            <div class="settings-section">
                <h4>消息机器人通知</h4>
                <p class="section-desc">当基站（挂载点上传端）或移动站（用户连接/下载端）上线或下线时，向钉钉或企业微信群机器人发送消息提醒。</p>
                <div id="notification-bots-list">
                    <p class="loading-text">正在加载机器人配置...</p>
                </div>
                <button onclick="showAddNotificationBotForm()" class="btn btn-primary" style="margin-top: 10px;">添加消息机器人</button>
            </div>

            <div class="settings-section">
                <h4>系统控制</h4>
                <button onclick="restartProgram()" class="btn btn-warning" style="background-color: #f39c12; border-color: #f39c12;">重启程序</button>
            </div>
        </div>
    `;
}

async function loadAnonymousSetting() {
    try {
        const response = await fetch('/api/settings/anonymous');
        const result = await handleApiResponse(response);
        const checkbox = document.getElementById('anonymous-access');
        const status = document.getElementById('anonymous-status');
        if (checkbox && result.success) {
            checkbox.checked = result.enabled;
        }
        if (status) {
            status.textContent = result.enabled ? '（已开启）' : '（已关闭）';
        }
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            // console.error('加载匿名访问设置失败:', error);
        }
    }
}

async function toggleAnonymousAccess() {
    const checkbox = document.getElementById('anonymous-access');
    const status = document.getElementById('anonymous-status');
    if (!checkbox) return;

    const enabled = checkbox.checked;
    try {
        const response = await fetch('/api/settings/anonymous', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: enabled })
        });
        const result = await handleApiResponse(response);
        if (result.success) {
            showAlert(`匿名访问已${result.enabled ? '开启' : '关闭'}`, 'success');
            if (status) {
                status.textContent = result.enabled ? '（已开启）' : '（已关闭）';
            }
        } else {
            showAlert('更新失败：' + (result.error || '未知错误'), 'error');
            checkbox.checked = !enabled;
        }
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('匿名访问更新失败：' + error.message, 'error');
        }
        checkbox.checked = !enabled;
    }
}

// Socket.IO

socket.on('log_message', function(data) {
    addLogLine(data.message, data.type);
});

// user
socket.on('online_users_update', function(data) {
    window.onlineUsers = data.users;
    updateOnlineStatus();
});

// mounts
socket.on('online_mounts_update', function(data) {
    window.onlineMounts = data.mounts;
    updateOnlineStatus();
});

// STR
socket.on('str_data_update', function(data) {
    window.strData = data.str_data;
    updateMonitorData();
});

// system
socket.on('system_stats_update', function(data) {
    if (currentPage === 'dashboard') {
        updateSystemStats(data.stats);
    } else if (currentPage === 'monitor') {
        updateMonitorStatus(data.stats);
    }
});

// 调试RTCM 数据
socket.on('rtcm_realtime_data', function(data) {
    // console.log('[前端接收] 收到RTCM实时数据:', data);
    // console.log('[前端接收] 数据类型:', typeof data);
    // console.log('[前端接收] 数据键:', Object.keys(data || {}));
    
    // 调试信息：显示除MSM类型外的其他数据类型
    if (data && data.data_type && data.data_type !== 'msm_satellite') {
        // console.log('[调试] 收到非MSM数据:', {
        //     数据类型: data.data_type,
        //     挂载点: data.mount_name || data.mount,
        //     时间戳: data.timestamp,
        //     数据内容: data
        // });
    }
    
    // 特别关注天线和设备信息
    if (data && data.data_type && ['device_info', 'antenna_info', 'receiver_info'].includes(data.data_type)) {
        // console.log('[天线设备调试] 收到天线/设备信息:', {
        //     数据类型: data.data_type,
        //     挂载点: data.mount_name || data.mount,
        //     接收机: data.receiver,
        //     固件: data.firmware,
        //     天线: data.antenna,
        //     天线序列号: data.antenna_firmware || data.antenna_serial,
        //     完整数据: data
        // });
    }
    
    if (!data || !data.data_type) {
        // console.warn('收到无效的RTCM数据:', data);
        return;
    }
    
    try {
        switch (data.data_type) {
            case 'station_position':
                // 处理基准站位置信息
                if (data.latitude && data.longitude) {
                    // console.log(`收到位置信息: ${data.latitude}, ${data.longitude}`);
                    
                    
                    if (!currentMap && currentPage === 'monitor') {
                        initializeMap();
                    }
                    
                    handlePositionUpdate(data.latitude, data.longitude);
                    
                    
                    updateElement('station-latitude', data.latitude.toFixed(6));
                    updateElement('station-longitude', data.longitude.toFixed(6));
                }
                break;
                
            case 'station_info':
               
                // console.log('收到基准站信息:', data);
                displayStationInfo(data);
                break;
                
            case 'msm_satellite':
               
                // console.log('收到卫星信号数据:', data);
                if (data.gnss && data.sats && Array.isArray(data.sats)) {
                    // 确保卫星可视化容器已初始化（只在第一次初始化）
                    if (currentPage === 'monitor') {
                        const satelliteContainer = document.getElementById('satellite-container');
                        if (satelliteContainer && !satelliteContainer.querySelector('.constellation-container')) {
                            initializeSatelliteVisualization();
                        }
                        
                        
                        const rtcmSatellites = data.sats.map(sat => ({
                            name: sat.id || sat.prn || '未知',
                            signalStrength: sat.snr || sat.signal_strength || 0,
                            frequency: sat.frequency || 0,
                            channel: sat.signal_type || '未知'
                        }));
                        
                        
                        let constellation = data.gnss.toUpperCase();
                        if (constellation === 'BDS' || constellation === 'BEIDOU') {
                            constellation = 'BDS';
                        } else if (constellation === 'GLONASS' || constellation === 'GLO') {
                            constellation = 'GLONASS';
                        } else if (constellation === 'GPS') {
                            constellation = 'GPS';
                        } else if (constellation === 'GALILEO') {
                            constellation = 'GALILEO';
                        } else if (constellation === 'QZSS') {
                            constellation = 'QZSS';
                        } else if (constellation === 'IRNSS') {
                            constellation = 'IRNSS';
                        } else if (constellation === 'NAVIC' || constellation === 'NAV') {
                            constellation = 'NAVIC';
                        }
                        
                        updateSatelliteVisualization(constellation, rtcmSatellites);
                    }
                }
                break;
                
            case 'geography':
                // （1005/1006）
                // console.log('[地理信息调试] 收到地理位置信息:', data);
    // console.log('[地理信息调试] 当前页面:', currentPage);
                
                // 只在monitor页面处理基准站信息显示
                if (currentPage !== 'monitor') {
                    // console.log('[地理信息调试] 不在monitor页面，跳过基准站信息显示');
                    break;
                }
                
                
                const stationInfoDiv = document.getElementById('station-info');
                // console.log('[地理信息调试] station-info元素:', stationInfoDiv);
    // console.log('[地理信息调试] station-info内容:', stationInfoDiv ? stationInfoDiv.innerHTML : 'station-info不存在');
    // console.log('[地理信息调试] 是否有empty-state:', stationInfoDiv ? stationInfoDiv.querySelector('.empty-state') : 'station-info不存在');
    // console.log('[地理信息调试] 是否有station-details:', stationInfoDiv ? stationInfoDiv.querySelector('.station-details') : 'station-info不存在');
                
                if (stationInfoDiv && (stationInfoDiv.querySelector('.empty-state') || !stationInfoDiv.querySelector('.station-details'))) {
                    // 如果还是空状态，先创建基础结构
                    // console.log('[地理信息调试] 检测到empty-state，创建基础结构');
                    const stationData = {
                        name: data.mount_name || data.mount || '未知',
                        id: data.station_id || '未知',
                        country: data.country || '未知',
                        city: data.city || '未知',
                        latitude: data.lat || 0,
                        longitude: data.lon || 0,
                        height: data.height || '未知',
                        x: data.x || 0,
                        y: data.y || 0,
                        z: data.z || 0,
                        receiver: { name: '未知', firmware: '未知' },
                        antenna: { name: '未知', serial: '未知' }
                    };
                    // console.log('[地理信息调试] 准备显示基准站信息:', stationData);
                    displayStationInfo(stationData);
                } else {
                    // 如果结构已存在，直接更新数据
                    // console.log('[地理信息调试] 基础结构已存在，更新数据');
        // console.log('[地理信息调试] 完整数据内容:', data);
                    
                    
                    if (data.mount_name || data.mount) {
                        // console.log('[地理信息调试] 更新挂载点名称:', data.mount_name || data.mount);
                        updateElement('station-name', data.mount_name || data.mount);
                    }
                    
                    
                    if (data.station_id !== undefined) {
                        // console.log('[地理信息调试] 更新基准站ID:', data.station_id);
                        updateElement('station-id', data.station_id.toString());
                    }
                    
                    
                    if (data.lat !== undefined && data.lon !== undefined) {
                        // console.log('[地理信息调试] 更新经纬度:', data.lat, data.lon);
                        
                        // 存储当前挂载点名称
                        currentMountName = data.mount_name || data.mount || null;
                        
                        
                        if (!currentMap && currentPage === 'monitor') {
                            initializeMap();
                        }
                        
                        handlePositionUpdate(data.lat, data.lon, currentMountName);
                        updateElement('station-latitude', data.lat.toFixed(6));
                        updateElement('station-longitude', data.lon.toFixed(6));
                    }
                    
                   
                    if (data.height !== undefined) {
                        // console.log('[地理信息调试] 更新高程:', data.height);
                        updateElement('station-height', data.height.toFixed(3) + ' m');
                    }
                    
                    // ECEF  XYZ
                    if (data.x !== undefined && data.y !== undefined && data.z !== undefined) {
                        // console.log('[地理信息调试] 更新XYZ坐标:', data.x, data.y, data.z);
                        updateElement('station-xyz', `X: ${data.x.toFixed(3)}, Y: ${data.y.toFixed(3)}, Z: ${data.z.toFixed(3)}`);
                    }
                    
                    // country
                    if (data.country || data.country_name) {
                        // console.log('[地理信息调试] 更新国家:', data.country_name || data.country);
                        updateElement('station-country', data.country_name || '未知');
                    }
                    
                    // city
                    if (data.city) {
                        // console.log('[地理信息调试] 更新城市:', data.city);
                        updateElement('station-city', data.city);
                    }
                }
                break;
                
            case 'device_info':
                // （1033）
                // console.log('收到设备信息:', data);
                if (data.receiver) {
                    updateElement('receiver-type', data.receiver);
                }
                if (data.firmware) {
                    updateElement('receiver-version', data.firmware);
                }
                if (data.antenna) {
                    updateElement('antenna-type', data.antenna);
                }
                if (data.antenna_firmware) {
                    updateElement('antenna-serial', data.antenna_firmware);
                }
                break;
                
            case 'antenna_info':
                // console.log('收到天线信息:', data);
                if (data.antenna_type) {
                    updateElement('antenna-type', data.antenna_type);
                }
                if (data.antenna_serial) {
                    updateElement('antenna-serial', data.antenna_serial);
                }
                break;
                
            case 'receiver_info':
                // console.log('收到接收机信息:', data);
                if (data.receiver_type) {
                    updateElement('receiver-type', data.receiver_type);
                }
                if (data.receiver_version) {
                    updateElement('receiver-version', data.receiver_version);
                }
                break;
                
            default:
                // console.log(`未处理的数据类型: ${data.data_type}`, data);
                break;
        }
    } catch (error) {
        // console.error('处理RTCM数据时发生错误:', error, data);
    }
});


function updateElement(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value;
    }
}


function updateSystemStats(stats) {
    if (!stats) return;
    
    const timestamp = new Date().toLocaleString('zh-CN');
    updateElement('dashboard-timestamp', `最后更新：${timestamp}`);
    
    if (stats.uptime !== undefined) {
        updateElement('system-uptime', formatUptime(stats.uptime));
    }
    
    if (stats.cpu_percent !== undefined) {
        updateElement('system-cpu', `${stats.cpu_percent.toFixed(1)}%`);
    }
    
    if (stats.memory) {
        const memUsed = (stats.memory.used / (1024 * 1024 * 1024)).toFixed(1);
        const memTotal = (stats.memory.total / (1024 * 1024 * 1024)).toFixed(1);
        const memPercent = stats.memory.percent.toFixed(1);
        updateElement('system-memory', `${memPercent}%`);
        updateElement('system-memory-detail', `${memUsed}GB / ${memTotal}GB`);
    }
    
    if (stats.network_bandwidth) {
        const bandwidth = stats.network_bandwidth;
        let bandwidthText = '';
        if (bandwidth.sent_rate || bandwidth.recv_rate) {
            const sent = formatBytes(bandwidth.sent_rate);
            const recv = formatBytes(bandwidth.recv_rate);
            bandwidthText = `↑${sent}/s ↓${recv}/s`;
        } else {
            bandwidthText = '0 B/s';
        }
        updateElement('system-bandwidth', bandwidthText);
    }
    

    if (stats.connections) {
        const conn = stats.connections;
        updateElement('active-connections', conn.active || 0);
        updateElement('max-connections', conn.max_concurrent || 0);
        updateElement('total-connections', conn.total || 0);
        updateElement('rejected-connections', conn.rejected || 0);
    }
    
    if (stats.mounts) {
        updateElement('total-mounts', Object.keys(stats.mounts).length);
        updateMountDetails(stats.mounts);
    }
    
    if (stats.users) {
        updateElement('total-users', stats.users.length);
    }
    
    if (stats.data_transfer) {
        const transfer = stats.data_transfer;
        const totalData = formatBytes(transfer.total_bytes || 0);
        updateElement('total-data', totalData);
    }
}


function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}


function requestSystemStats() {
    socket.emit('request_system_stats');
}

// （API）
async function fetchSystemStats() {
    try {
        const response = await fetch('/api/system/stats');
        if (response.ok) {
            const stats = await response.json();
            updateSystemStats(stats);
        } else {
            // console.error('获取系统统计数据失败:', response.status);
        }
    } catch (error) {
        // console.error('获取系统统计数据异常:', error);
    }
}


function updateMountDetails(mounts) {
    const container = document.getElementById('mounts-detail');
    if (!container) return;
    
    if (!mounts || mounts.length === 0) {
        container.innerHTML = '<div class="loading-text">暂无挂载点数据</div>';
        return;
    }
    
    const mountsHtml = mounts.map(mount => {
        const mountName = mount.mount_name || '未知';
        const userCount = mount.user_count || 0;
        const dataCount = mount.data_count || 0;
        const uptime = mount.uptime || 0;
        const status = mount.status || 'unknown';
        
        // time
        const uptimeStr = formatUptime(uptime);
        
        return `
            <div class="mount-item">
                <div class="mount-name">${mountName}</div>
                <div class="mount-stats">
                    <div>👤 ${userCount} 用户</div>
            <div>📈 ${dataCount} 数据包</div>
                    <div>⏱️ ${uptimeStr}</div>
                    <div>⚙️ ${status}</div>
                </div>
            </div>
        `;
    }).join('');
    
    container.innerHTML = mountsHtml;
}


function formatUptime(seconds) {
    if (!seconds || seconds < 0) return '0s';
    
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    
    if (days > 0) {
        return `${days}天 ${hours}小时 ${minutes}分`;
    } else if (hours > 0) {
        return `${hours}小时 ${minutes}分`;
    } else if (minutes > 0) {
        return `${minutes}分 ${secs}秒`;
    } else {
        return `${secs}秒`;
    }
}

// 
function updateOnlineStatus() {
    // 移动站页面：检测连接变化，自动刷新列表
    if (currentPage === 'users') {
        // 获取当前表格中的连接ID集合
        const tableRows = document.querySelectorAll('#mobile-station-table tbody tr[data-connection-id]');
        const currentConnectionIds = new Set();
        tableRows.forEach(row => {
            currentConnectionIds.add(row.dataset.connectionId);
        });
        
        // 获取socket推送的连接ID集合
        const socketConnectionIds = new Set();
        if (window.onlineUsers) {
            Object.values(window.onlineUsers).forEach(conns => {
                conns.forEach(conn => {
                    if (conn.connection_id) {
                        socketConnectionIds.add(conn.connection_id);
                    }
                });
            });
        }
        
        // 比较连接ID集合：数量不同或具体ID不同都需要刷新
        const needsRefresh = currentConnectionIds.size !== socketConnectionIds.size || 
            [...currentConnectionIds].some(id => !socketConnectionIds.has(id)) ||
            [...socketConnectionIds].some(id => !currentConnectionIds.has(id));
        
        if (needsRefresh) {
            // 连接有新增或删除，刷新整个列表
            loadPageContent('users');
        } else {
            // 连接没有变化，只更新差分状态和数据速率
            updateMobileStationDiffingStatus();
        }
    }
    
    // 挂载点页面：检测挂载点上下线变化，自动刷新列表
    if (currentPage === 'mounts') {
        // 获取当前表格中的挂载点名称集合
        const mountRows = document.querySelectorAll('.mount-row');
        const currentMountNames = new Set();
        mountRows.forEach(row => {
            currentMountNames.add(row.dataset.mount);
        });
        
        // 获取socket推送的在线挂载点名称集合
        const socketMountNames = new Set();
        if (window.onlineMounts) {
            Object.keys(window.onlineMounts).forEach(mountName => {
                socketMountNames.add(mountName);
            });
        }
        
        // 检测挂载点是否有新增或删除（上下线）
        const mountsAdded = [...socketMountNames].some(name => !currentMountNames.has(name));
        const mountsRemoved = [...currentMountNames].some(name => !socketMountNames.has(name));
        
        if (mountsAdded || mountsRemoved) {
            // 挂载点有新增或删除，刷新整个列表
            loadPageContent('mounts');
        } else {
            // 挂载点没有变化，只更新在线状态和连接数
            mountRows.forEach(row => {
                const mountName = row.dataset.mount;
                const statusElement = row.querySelector('.mount-status');
                const connectionsCell = row.querySelector('.mount-connections');
                
                if (window.onlineMounts) {
                    const isOnline = mountName in window.onlineMounts;
                    const mountData = window.onlineMounts[mountName];
                    
                    if (statusElement) {
                        statusElement.innerHTML = isOnline ? 
                            '<span style="color: #28a745; font-weight: bold;">● 在线</span>' : 
                            '<span style="color: #6c757d;">○ 离线</span>';
                    }
                    
                    if (connectionsCell && mountData) {
                        const connectionCount = mountData.connections || mountData.connection_count || 0;
                        connectionsCell.textContent = connectionCount;
                    }
                }
            });
        }
    }
    
    updateDashboardCounts();
}

//
function updateDashboardCounts() {
    // 移动站连接数（从在线用户连接统计）
    const mobileStationCount = window.onlineUsers ? Object.values(window.onlineUsers).reduce((sum, conns) => sum + (conns ? conns.length : 0), 0) : 0;
    const totalUsersElement = document.getElementById('total-users');
    if (totalUsersElement) {
        totalUsersElement.textContent = mobileStationCount;
    }
    
    // mounts
    const activeMountsCount = window.onlineMounts ? Object.keys(window.onlineMounts).length : 0;
    const dashboardActiveMountsElement = document.getElementById('dashboard-active-mounts');
    if (dashboardActiveMountsElement) {
        dashboardActiveMountsElement.textContent = activeMountsCount;
    }
}

// INFO Buttons
                setTimeout(() => {
                    addInfoButtonsToSTRItems();
                }, 200);


function updateMonitorData() {
    if (currentPage === 'monitor' && window.strData) {
        const strDataElement = document.getElementById('str-data');
        if (strDataElement) {
            if (Object.keys(window.strData).length === 0) {
                strDataElement.innerHTML = '<div class="empty-state"><i class="fas fa-table"></i><p>暂无 STR 表数据</p></div>';
            } else {
                let strHtml = '';
                Object.entries(window.strData).forEach(([mountName, strContent]) => {
                    strHtml += `
                        <div class="str-row">
                            <button class="str-info-btn" data-mount="${mountName}">信息</button>
                            <div class="str-content-wrapper">
                                <div class="str-content-inline">${strContent || '暂无数据'}</div>
                            </div>
                        </div>
                    `;

                });
                strDataElement.innerHTML = strHtml;
                
                addInfoButtonsToSTRItems();
            }
        }
    }
}


function refreshSTRData() {
    const strContainer = document.getElementById('str-data');
    if (strContainer) {
        strContainer.innerHTML = '<p class="loading-text"><i class="fas fa-spinner fa-spin"></i> 正在刷新 STR 表数据...</p>';
    }
    
    
    if (socket && socket.connected) {
        socket.emit('request_str_data');
    }
}


function updateMonitorStatus(systemStatus) {
    
    const connectionStatus = document.getElementById('connection-status-monitor');
    if (connectionStatus) {
        connectionStatus.textContent = socket && socket.connected ? '已连接' : '已断开';
    }
    
    
    const runtime = document.getElementById('runtime-monitor');
    if (runtime && systemStatus && systemStatus.uptime) {
        runtime.textContent = formatUptime(systemStatus.uptime);
    }
    
    
    const dataFlow = document.getElementById('data-flow-monitor');
    if (dataFlow && systemStatus && systemStatus.total_bytes) {
        dataFlow.textContent = formatBytes(systemStatus.total_bytes);
    }
}


function updateStationStatus(hasData) {
    const stationStatus = document.getElementById('station-status');
    if (stationStatus) {
        const statusDot = stationStatus.querySelector('.status-dot');
        const statusText = stationStatus.querySelector('span:last-child');
        
        if (hasData) {
            statusDot.className = 'status-dot online';
            statusText.textContent = '已选择';
        } else {
            statusDot.className = 'status-dot waiting';
            statusText.textContent = '等待选择';
        }
    }
}


function updateSatelliteStatus(hasData) {
    const satelliteStatus = document.getElementById('satellite-status');
    if (satelliteStatus) {
        const statusDot = satelliteStatus.querySelector('.status-dot');
        const statusText = satelliteStatus.querySelector('span:last-child');
        
        if (hasData) {
            statusDot.className = 'status-dot online';
            statusText.textContent = '接收中';
        } else {
            statusDot.className = 'status-dot waiting';
            statusText.textContent = '等待数据';
        }
    }
}


function validateAlphanumeric(input, fieldName) {
   
    const validPattern = /^[a-zA-Z0-9_-]+$/;
    
    if (!input || input.trim() === '') {
        return { valid: false, message: `${fieldName}不能为空` };
    }
    
    if (!validPattern.test(input)) {
        return { valid: false, message: `${fieldName}只能包含英文字母、数字、下划线和短横线，不允许其他特殊符号、中文或其他字符` };
    }
    
    return { valid: true, message: '' };
}

// Add log line
// 局部刷新移动站差分状态（不刷新整个列表）
function updateMobileStationDiffingStatus() {
    if (!window.onlineUsers) return;
    
    const rows = document.querySelectorAll('#mobile-station-table tbody tr[data-connection-id]');
    rows.forEach(row => {
        const connectionId = row.dataset.connectionId;
        const username = row.dataset.username;
        if (!connectionId || !username) return;
        
        // 从 onlineUsers 中查找连接状态
        const userConns = window.onlineUsers[username];
        if (!userConns) return;
        
        const conn = userConns.find(c => c.connection_id === connectionId);
        if (!conn) return;
        
        // 更新差分状态（GGA质量码）
        const diffCell = row.querySelector('.diff-status-cell');
        if (diffCell) {
            const ggaQuality = conn.gga_quality;
            const ggaInfo = ggaQuality !== null && ggaQuality !== undefined ? 
                (GGA_QUALITY_MAP[ggaQuality] || { label: ggaQuality + '-未知', color: '#6c757d' }) :
                { label: '-', color: '#6c757d' };
            diffCell.textContent = ggaInfo.label;
            diffCell.style.color = ggaInfo.color;
        }
        
        // 实时更新已发送数据量
        const bytesSentCell = row.cells[5];
        if (bytesSentCell && conn.bytes_sent !== undefined) {
            bytesSentCell.textContent = formatBytes(conn.bytes_sent);
        }
        
        // 实时更新发送速率
        const dataRateCell = row.cells[6];
        if (dataRateCell && conn.data_rate !== undefined) {
            dataRateCell.textContent = formatDataRate(conn.data_rate);
        }
    });
}

// 初始化可搜索下拉框
function initSearchableDropdown(containerId, hiddenInputId, inputId, listId, onSelect) {
    const container = document.getElementById(containerId);
    const input = document.getElementById(inputId);
    const list = document.getElementById(listId);
    const hiddenInput = document.getElementById(hiddenInputId);
    
    if (!container || !input || !list || !hiddenInput) return;
    
    // Show dropdown on focus
    input.addEventListener('focus', function() {
        list.style.display = 'block';
        filterDropdownItems(input.value, list);
    });
    
    // Filter items on input
    input.addEventListener('input', function() {
        filterDropdownItems(this.value, list);
        list.style.display = 'block';
    });
    
    // Handle item click
    list.addEventListener('click', function(e) {
        const item = e.target.closest('.dropdown-item');
        if (item) {
            const value = item.getAttribute('data-value');
            const text = item.textContent;
            input.value = text === '全部挂载点' || text === '全部用户' ? '' : text;
            hiddenInput.value = value;
            list.style.display = 'none';
            if (onSelect) onSelect(value);
        }
    });
    
    // Close dropdown when clicking outside
    document.addEventListener('click', function(e) {
        if (!container.contains(e.target)) {
            list.style.display = 'none';
        }
    });
    
    // Handle keyboard navigation
    input.addEventListener('keydown', function(e) {
        const items = list.querySelectorAll('.dropdown-item:not([style*="display: none"])');
        let activeIndex = Array.from(items).findIndex(item => item.classList.contains('active'));
        
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            list.style.display = 'block';
            if (activeIndex < items.length - 1) activeIndex++;
            else activeIndex = 0;
            setActiveItem(items, activeIndex);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (activeIndex > 0) activeIndex--;
            else activeIndex = items.length - 1;
            setActiveItem(items, activeIndex);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            const activeItem = list.querySelector('.dropdown-item.active');
            if (activeItem) {
                activeItem.click();
            }
        } else if (e.key === 'Escape') {
            list.style.display = 'none';
        }
    });
}

function filterDropdownItems(searchText, list) {
    const items = list.querySelectorAll('.dropdown-item');
    const lowerSearch = searchText.toLowerCase();
    items.forEach(item => {
        const text = item.textContent.toLowerCase();
        if (text.includes(lowerSearch)) {
            item.style.display = '';
        } else {
            item.style.display = 'none';
        }
    });
}

function setActiveItem(items, index) {
    items.forEach(item => item.classList.remove('active'));
    if (items[index]) {
        items[index].classList.add('active');
        items[index].style.background = '#e9ecef';
    }
    items.forEach((item, i) => {
        if (i !== index) item.style.background = '';
    });
}

// Debounce utility function
function debounce(func, wait) {
    let timeout;
    return function(...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
    };
}

// Debounced scroll function
let scrollTimeout = null;
function debouncedScroll(container) {
    if (scrollTimeout) {
        clearTimeout(scrollTimeout);
    }
    scrollTimeout = setTimeout(() => {
        container.scrollTop = container.scrollHeight;
    }, 10);
}

function addLogLine(message, type = 'info') {
    const logContainer = document.getElementById('log-terminal');
    if (logContainer) {
        // Use requestAnimationFrame to ensure DOM updates at appropriate time
        requestAnimationFrame(() => {
            const logEntry = document.createElement('div');
            logEntry.className = `log-line ${type}`;
            logEntry.textContent = `[${type.toUpperCase()}] ${message}`;
            
            // Disable animation to avoid flickering
            logEntry.style.animation = 'none';
            logEntry.style.transform = 'translateZ(0)'; // Enable hardware acceleration
            logEntry.style.willChange = 'auto';
            
            // Add directly to container, avoid extra overhead of document fragments
            logContainer.appendChild(logEntry);
            
            // Use debounced scroll
            debouncedScroll(logContainer);
            
            // Limit log entries, batch delete to reduce reflow
            const logEntries = logContainer.children;
            if (logEntries.length > 100) {
                // Delete first 10 entries to reduce frequent deletions
                requestAnimationFrame(() => {
                    for (let i = 0; i < 10 && logContainer.firstChild; i++) {
                        logContainer.removeChild(logContainer.firstChild);
                    }
                });
            }
        });
    }
}

// Initialization after page load completion
document.addEventListener('DOMContentLoaded', function() {
    // Initialize page
    navigateTo('dashboard');
    
    // Load frequency mapping table
    loadFrequencyMap();
    

    
    // Load application information
    loadAppInfo();
    
    // Navigation event listeners
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            const page = this.getAttribute('data-page');
            if (page) {
                navigateTo(page);
            }
        });
    });
});

// User management functions
function showAddUserForm() {
    const formHtml = `
        <div class="modal-overlay" id="userModal">
            <div class="modal-content">
                <h4>添加用户</h4>
                <div class="form-group">
                    <label>用户名</label>
                    <input type="text" id="newUsername" placeholder="请输入用户名" maxlength="50">
                </div>
                <div class="form-group">
                    <label>密码</label>
                    <input type="password" id="newPassword" placeholder="请输入密码" maxlength="100">
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('userModal')">取消</button>
                    <button class="btn btn-success" onclick="submitAddUser()">添加</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

async function submitAddUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    
    // username
    const usernameValidation = validateAlphanumeric(username, '用户名');
    if (!usernameValidation.valid) {
        showAlert(usernameValidation.message, 'error');
        return;
    }
    
    if (username.length < 3 || username.length > 50) {
        showAlert('用户名长度必须在 3-50 个字符之间', 'error');
        return;
    }
    
    // password
    const passwordValidation = validateAlphanumeric(password, '密码');
    if (!passwordValidation.valid) {
        showAlert(passwordValidation.message, 'error');
        return;
    }
    
    if (password.length < 6 || password.length > 100) {
        showAlert('密码长度必须在 6-100 个字符之间', 'error');
        return;
    }
    
    await addUser(username, password);
    closeModal('userModal');
}

async function addUser(username, password) {
    try {
        const response = await fetch('/api/users', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ username, password })
        });
        
        const result = await handleApiResponse(response);
        loadPageContent('users'); // Refresh user list
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('添加用户失败：' + error.message, 'error');
        }
    }
}

function editUser(username) {
    const isAdmin = username === 'admin';
    const formHtml = `
        <div class="modal-overlay" id="editUserModal">
            <div class="modal-content">
                <h4>编辑用户 - ${username}</h4>
                ${!isAdmin ? `
                <div class="form-group">
                    <label>用户名</label>
                    <input type="text" id="editUsername" value="${username}" maxlength="50">
                </div>
                ` : `
                <div class="form-group">
                    <label>用户名</label>
                    <input type="text" value="${username}" disabled>
                    <small>管理员用户名不能修改</small>
                </div>
                `}
                <div class="form-group">
                    <label>新密码（可选）</label>
                    <input type="password" id="editPassword" placeholder="留空表示保持当前密码" maxlength="100">
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('editUserModal')">取消</button>
                    <button class="btn btn-success" onclick="submitEditUser('${username}')">保存</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

function submitEditUser(originalUsername) {
    const newUsername = document.getElementById('editUsername')?.value.trim();
    const newPassword = document.getElementById('editPassword').value.trim();
    
    const updateData = {};
    
    // If password is entered, validate and add to update data
    if (newPassword) {
        const passwordValidation = validateAlphanumeric(newPassword, '密码');
        if (!passwordValidation.valid) {
            showAlert(passwordValidation.message, 'error');
            return;
        }
        if (newPassword.length < 6 || newPassword.length > 100) {
            showAlert('密码长度必须在 6-100 个字符之间', 'error');
            return;
        }
        updateData.password = newPassword;
    }
    
    // If not admin and username has changed
    if (originalUsername !== 'admin' && newUsername && newUsername !== originalUsername) {
        const usernameValidation = validateAlphanumeric(newUsername, '用户名');
        if (!usernameValidation.valid) {
            showAlert(usernameValidation.message, 'error');
            return;
        }
        if (newUsername.length < 3 || newUsername.length > 50) {
            showAlert('用户名长度必须在 3-50 个字符之间', 'error');
            return;
        }
        updateData.username = newUsername;
    }
    
    // Check if there are any updates
    if (Object.keys(updateData).length === 0) {
        showAlert('未做任何修改', 'warning');
        return;
    }
    
    updateUser(originalUsername, updateData);
    closeModal('editUserModal');
}

async function updateUser(username, data) {
    try {
        const response = await fetch(`/api/users/${username}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });                
        const result = await handleApiResponse(response);
        showAlert(result.message, 'success');
        loadPageContent('users'); // Refresh user list
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('更新用户失败：' + error.message, 'error');
        }
    }
}

function deleteUser(username) {
    // console.log('deleteUser called with username:', username);
    showConfirmDialog(
        '确认删除用户',
        `确定要删除用户 "${username}" 吗？此操作无法撤销。`,
        () => {
            // console.log('User confirmed deletion');
            removeUser(username);
        },
        () => {
            // console.log('User cancelled deletion');
        }
    );
}

async function removeUser(username) {
    // console.log('removeUser called with username:', username);
    try {
        // console.log('Sending DELETE request to:', `/api/users/${username}`);
        const response = await fetch(`/api/users/${username}`, {
            method: 'DELETE'
        });
        
        // console.log('Response status:', response.status);
        const result = await handleApiResponse(response);
        // console.log('API response result:', result);
        // Refresh list directly after successful deletion, no success popup
        loadPageContent('users'); // Refresh user list
    } catch (error) {
        // console.error('Error in removeUser:', error);
        if (error.message !== 'Unauthorized access') {
            showAlert('删除用户失败：' + error.message, 'error');
        }
    }
}

// Mount point management functions
async function showAddMountForm() {
    // Get user list for dropdown selection
    let usersOptions = '<option value="">不绑定用户</option>';
    try {
        const response = await fetch('/api/users');
        if (response.ok) {
            const users = await response.json();
            users.forEach(user => {
                if (!user.anonymous && user.id !== -1) {
                    usersOptions += `<option value="${user.id}">${user.username}</option>`;
                }
            });
        }
    } catch (error) {
        // console.error('Failed to get user list:', error);
    }
    
    const formHtml = `
        <div class="modal-overlay" id="mountModal">
            <div class="modal-content">
                <h4>添加挂载点</h4>
                <div class="form-group">
                    <label>挂载点名称</label>
                    <input type="text" id="newMountName" placeholder="请输入挂载点名称" maxlength="50">
                </div>
                <div class="form-group">
                    <label>密码（NTRIP 1.0）</label>
                    <input type="password" id="newMountPassword" placeholder="请输入密码" maxlength="100">
                </div>
                <div class="form-group">
                    <label>绑定用户（NTRIP 2.0）</label>
                    <select id="newMountUser">
                        ${usersOptions}
                    </select>
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('mountModal')">取消</button>
                    <button class="btn btn-success" onclick="submitAddMount()">添加</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

function submitAddMount() {
    const mountName = document.getElementById('newMountName').value.trim();
    const password = document.getElementById('newMountPassword').value;
    const userId = document.getElementById('newMountUser').value;
    
    // 验证挂载点名称
    const mountNameValidation = validateAlphanumeric(mountName, '挂载点名称');
    if (!mountNameValidation.valid) {
        showAlert(mountNameValidation.message, 'error');
        return;
    }
    
    // 验证密码
    const passwordValidation = validateAlphanumeric(password, '密码');
    if (!passwordValidation.valid) {
        showAlert(passwordValidation.message, 'error');
        return;
    }
    
    if (mountName.length < 3 || mountName.length > 50) {
        showAlert('挂载点名称长度必须在 3-50 个字符之间', 'error');
        return;
    }
    
    if (password.length < 6 || password.length > 100) {
        showAlert('密码长度必须在 6-100 个字符之间', 'error');
        return;
    }
    
    const mountData = { mount: mountName, password: password };
    if (userId) {
        mountData.user_id = parseInt(userId);
    }
    
    addMount(mountData);
    closeModal('mountModal');
}

async function addMount(mountData) {
    try {
        const response = await fetch('/api/mounts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(mountData)
        });
        
        const result = await handleApiResponse(response);
        loadPageContent('mounts'); // Refresh mount point list
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('添加挂载点失败：' + error.message, 'error');
        }
    }
}

async function editMount(mount) {
    let currentMountData = null;
    let currentUsername = '';
    
    try {
        // Get current mount point information
        const mountResponse = await fetch('/api/mounts');
        if (mountResponse.ok) {
            const mounts = await mountResponse.json();
            currentMountData = mounts.find(m => m.mount === mount);
        }
        
        // If mount point is bound to a user, get username
        if (currentMountData && currentMountData.user_id && currentMountData.user_id !== -1) {
            const usersResponse = await fetch('/api/users');
            if (usersResponse.ok) {
                const users = await usersResponse.json();
                const currentUser = users.find(u => u.id === currentMountData.user_id && !u.anonymous);
                if (currentUser) {
                    currentUsername = currentUser.username;
                }
            }
        }
    } catch (error) {
        // console.error('Failed to get data:', error);
    }
    
    const formHtml = `
        <div class="modal-overlay" id="editMountModal">
            <div class="modal-content">
                <h4>编辑挂载点 - ${mount}</h4>
                <div class="form-group">
                    <label>挂载点名称</label>
                    <input type="text" id="editMountName" value="${mount}" maxlength="50">
                </div>
                <div class="form-group">
                    <label>新密码（NTRIP 1.0）</label>
                    <input type="password" id="editMountPassword" placeholder="留空表示保持当前密码" maxlength="100">
                </div>
                <div class="form-group">
                    <label>绑定用户（NTRIP 2.0）</label>
                    <input type="text" id="editMountUser" value="${currentUsername}" placeholder="请输入用户名，留空表示不绑定" maxlength="50">
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('editMountModal')">取消</button>
                    <button class="btn btn-success" onclick="submitEditMount('${mount}')">保存</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

async function submitEditMount(originalMount) {
    const newMountName = document.getElementById('editMountName').value.trim();
    const newPassword = document.getElementById('editMountPassword').value.trim();
    const username = document.getElementById('editMountUser').value.trim();
    
    const updateData = {};
    
    // 如果输入了密码，验证并加入更新数据
    if (newPassword) {
        const passwordValidation = validateAlphanumeric(newPassword, '密码');
        if (!passwordValidation.valid) {
            showAlert(passwordValidation.message, 'error');
            return;
        }
        if (newPassword.length < 6 || newPassword.length > 100) {
            showAlert('密码长度必须在 6-100 个字符之间', 'error');
            return;
        }
        updateData.password = newPassword;
    }
    
    // 如果挂载点名称已更改且不为空
    if (newMountName && newMountName !== originalMount) {
        const mountNameValidation = validateAlphanumeric(newMountName, '挂载点名称');
        if (!mountNameValidation.valid) {
            showAlert(mountNameValidation.message, 'error');
            return;
        }
        if (newMountName.length < 3 || newMountName.length > 50) {
            showAlert('挂载点名称长度必须在 3-50 个字符之间', 'error');
            return;
        }
        updateData.mount_name = newMountName;
    }
    
    // 处理用户绑定
    if (username) {
        const usernameValidation = validateAlphanumeric(username, '用户名');
        if (!usernameValidation.valid) {
            showAlert(usernameValidation.message, 'error');
            return;
        }
    }
    updateData.username = username || "";
    
    // 检查是否有任何更新
    if (Object.keys(updateData).length === 0) {
        showAlert('未做任何修改', 'warning');
        return;
    }
    
    updateMount(originalMount, updateData);
    closeModal('editMountModal');
}

async function updateMount(mount, data) {
    try {
        const response = await fetch(`/api/mounts/${mount}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(data)
        });
        
        const result = await handleApiResponse(response);
        showAlert(result.message, 'success');
        loadPageContent('mounts'); // Refresh mount point list
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('更新挂载点失败：' + error.message, 'error');
        }
    }
}

function deleteMount(mount) {
    showConfirmDialog(
        '确认删除挂载点',
        `确定要删除挂载点 "${mount}" 吗？此操作无法撤销。`,
        () => {
            removeMount(mount);
        },
        () => {
            // 用户取消删除
        }
    );
}

async function removeMount(mount) {
        try {
            const response = await fetch(`/api/mounts/${mount}`, {
                method: 'DELETE'
            });
            
            const result = await handleApiResponse(response);
            // Refresh list directly after successful deletion, no success popup
            loadPageContent('mounts'); // Refresh mount point list
        } catch (error) {
            if (error.message !== 'Unauthorized access') {
                showAlert('删除挂载点失败：' + error.message, 'error');
            }
        }
    }
    

    
    async function changePassword() {
        const newPassword = document.getElementById('admin-password').value;
        const confirmPassword = document.getElementById('confirm-password').value;
        
        if (!newPassword || !confirmPassword) {
            showAlert('请输入新密码和确认密码', 'warning');
            return;
        }
        
        // 验证新密码
        const passwordValidation = validateAlphanumeric(newPassword, '新密码');
        if (!passwordValidation.valid) {
            showAlert(passwordValidation.message, 'error');
            return;
        }
        
        // 验证确认密码
        const confirmPasswordValidation = validateAlphanumeric(confirmPassword, '确认密码');
        if (!confirmPasswordValidation.valid) {
            showAlert(confirmPasswordValidation.message, 'error');
            return;
        }
        
        if (newPassword !== confirmPassword) {
            showAlert('两次输入的密码不一致', 'error');
            return;
        }
        
        if (newPassword.length < 6) {
            showAlert('密码长度至少为 6 个字符', 'error');
            return;
        }
        
        try {
            const response = await fetch('/api/users/admin', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ password: newPassword })
            });
            
            const result = await response.json();
            
            if (response.ok) {
                showAlert('管理员密码修改成功', 'success');
                document.getElementById('admin-password').value = '';
                document.getElementById('confirm-password').value = '';
            } else {
                showAlert('错误：' + result.error, 'error');
            }
        } catch (error) {
            // console.error('修改密码失败:', error);
            showAlert('修改密码失败：' + error.message, 'error');
        }
    }
    
    async function restartProgram() {
        showConfirmDialog(
        '确认重启',
        '确定要重启程序吗？重启后将断开所有连接，请谨慎操作！',
        async function() {
            // 执行重启逻辑
            await performRestart();
        }
    );
}

async function performRestart() {
        
        try {
            const response = await fetch('/api/system/restart', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            if (response.ok) {
                showAlert('重启命令已发送，系统将在 3 秒后重启...', 'success');
                // 3 秒后刷新页面
                setTimeout(() => {
                    window.location.reload();
                }, 3000);
            } else {
                const result = await response.json();
                showAlert('重启失败：' + (result.error || '未知错误'), 'error');
            }
        } catch (error) {
            // console.error('重启程序失败:', error);
            showAlert('重启程序失败：' + error.message, 'error');
        }
    }


function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.remove();
    }
}


function showAlert(message, type = 'info') {
    const modalId = 'alertDialog';
    
    
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }
    
    const iconMap = {
        'info': 'ℹ️',
        'success': '✔️',
        'error': '✖️',
        'warning': '⚠️'
    };
    
    const colorMap = {
        'info': '#3498db',
        'success': '#27ae60',
        'error': '#e74c3c',
        'warning': '#f39c12'
    };
    
    const modalHtml = `
        <div class="modal-overlay" id="${modalId}">
            <div class="modal-content" style="max-width: 400px; text-align: center;">
                <div style="font-size: 2rem; margin-bottom: 1rem;">${iconMap[type] || iconMap['info']}</div>
                <p style="margin-bottom: 2rem; color: #666; line-height: 1.5; font-size: 1.1rem;">${message}</p>
                <div style="display: flex; justify-content: center;">
                    <button class="btn" style="background: ${colorMap[type] || colorMap['info']}; color: white; border: none;" onclick="closeModal('${modalId}')">确定</button>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Click background to close
    document.getElementById(modalId).addEventListener('click', function(e) {
        if (e.target === this) {
            closeModal(modalId);
        }
    });
    
    // Close with ESC key
    const escHandler = function(e) {
        if (e.key === 'Escape') {
            closeModal(modalId);
            document.removeEventListener('keydown', escHandler);
        }
    };
    document.addEventListener('keydown', escHandler);
}

// Show confirmation dialog
function showConfirmDialog(title, message, onConfirm, onCancel) {
    const modalId = 'confirmDialog';
    
    // Remove existing confirmation dialog
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }
    
    const modalHtml = `
        <div class="modal-overlay" id="${modalId}">
            <div class="modal-content" style="max-width: 400px;">
                <h4>${title}</h4>
                <p style="margin-bottom: 2rem; color: #666; line-height: 1.5;">${message}</p>
                <div style="display: flex; gap: 1rem; justify-content: flex-end;">
                    <button class="btn btn-secondary" onclick="cancelConfirm()">取消</button>
                    <button class="btn btn-primary" onclick="confirmAction()">确定</button>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    // Temporarily store callback functions
    window.tempConfirmCallback = onConfirm;
    window.tempCancelCallback = onCancel;
    
    
    document.getElementById(modalId).addEventListener('click', function(e) {
        if (e.target === this) {
            cancelConfirm();
        }
    });
}

// Confirm action
function confirmAction() {
    if (window.tempConfirmCallback) {
        window.tempConfirmCallback();
    }
    closeModal('confirmDialog');
    // Clean up temporary callbacks
    window.tempConfirmCallback = null;
    window.tempCancelCallback = null;
}

// Cancel action
function cancelConfirm() {
    if (window.tempCancelCallback) {
        window.tempCancelCallback();
    }
    closeModal('confirmDialog');
    // Clean up temporary callbacks
    window.tempConfirmCallback = null;
    window.tempCancelCallback = null;
}
    
    // Click modal background to close
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('modal-overlay')) {
            e.target.remove();
        }
    });
    
    // Close modal with ESC key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            const modals = document.querySelectorAll('.modal-overlay');
            modals.forEach(modal => modal.remove());
        }
    });
    
    // 登出函数
    function logout() {
        // 简化登出流程，直接执行登出操作
        showConfirmDialog(
            '确认登出',
            '确定要登出吗？',
            () => {
                fetch('/logout', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                }).then(() => {
                    window.location.href = '/login';
                }).catch(error => {
                     // console.error('登出失败:', error);
                     window.location.href = '/login';
                 });
             },
             () => {
                 // 用户取消登出
             }
         );
    }


// ==================== 消息机器人通知功能 ====================

async function loadNotificationBots() {
    try {
        const response = await fetch('/api/notification/bots');
        const result = await handleApiResponse(response);
        if (result.success) {
            renderNotificationBots(result.bots || []);
        } else {
            showAlert('加载消息机器人配置失败：' + (result.error || '未知错误'), 'error');
        }
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('加载消息机器人配置失败：' + error.message, 'error');
        }
    }
}

function renderNotificationBots(bots) {
    const container = document.getElementById('notification-bots-list');
    if (!container) return;

    if (!bots || bots.length === 0) {
        container.innerHTML = '<p class="empty-text" style="color: #6c757d; padding: 10px 0;">暂无消息机器人配置，点击下方按钮添加。</p>';
        return;
    }

    const eventLabels = {
        'base_station_online': '基站上线',
        'base_station_offline': '基站下线',
        'mount_online': '移动站上线',
        'mount_offline': '移动站下线'
    };

    const platformLabels = {
        'dingtalk': '钉钉',
        'wecom': '企业微信'
    };

    const html = bots.map(bot => {
        const eventTags = (bot.events || []).map(e => `<span class="event-tag">${eventLabels[e] || e}</span>`).join('');
        const enabledText = bot.enabled ? '<span style="color: #28a745;">已启用</span>' : '<span style="color: #dc3545;">已禁用</span>';
        return `
            <div class="bot-card" style="border: 1px solid #e9ecef; border-radius: 6px; padding: 12px 15px; margin-bottom: 10px; background: #fafbfc;">
                <div class="bot-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div style="font-weight: bold; color: #333;">${escapeHtml(bot.name)}</div>
                    <div style="font-size: 0.85em;">${platformLabels[bot.platform] || bot.platform} · ${enabledText}</div>
                </div>
                <div class="bot-url" style="font-size: 0.85em; color: #6c757d; margin-bottom: 8px; word-break: break-all;">${escapeHtml(bot.webhook_url)}</div>
                <div class="bot-events" style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;">${eventTags}</div>
                <div class="bot-actions" style="display: flex; gap: 8px;">
                    <button class="btn btn-primary btn-sm" onclick="editNotificationBot(${bot.id})">编辑</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteNotificationBot(${bot.id})">删除</button>
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showAddNotificationBotForm() {
    showNotificationBotModal();
}

function showNotificationBotModal(bot = null) {
    const isEdit = bot !== null;
    const title = isEdit ? '编辑消息机器人' : '添加消息机器人';
    const name = bot ? bot.name : '';
    const platform = bot ? bot.platform : 'dingtalk';
    const webhookUrl = bot ? bot.webhook_url : '';
    const secret = bot ? (bot.secret || '') : '';
    const enabled = bot ? bot.enabled : true;
    const events = bot ? (bot.events || []) : [];

    const formHtml = `
        <div class="modal-overlay" id="botModal">
            <div class="modal-content" style="max-width: 500px;">
                <h4>${title}</h4>
                <div class="form-group">
                    <label>机器人名称</label>
                    <input type="text" id="botName" value="${escapeHtml(name)}" placeholder="例如：运维通知群" maxlength="50">
                </div>
                <div class="form-group">
                    <label>平台</label>
                    <select id="botPlatform">
                        <option value="dingtalk" ${platform === 'dingtalk' ? 'selected' : ''}>钉钉</option>
                        <option value="wecom" ${platform === 'wecom' ? 'selected' : ''}>企业微信</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Webhook 地址</label>
                    <input type="text" id="botWebhookUrl" value="${escapeHtml(webhookUrl)}" placeholder="https://oapi.dingtalk.com/robot/send?access_token=xxx">
                </div>
                <div class="form-group" id="secretFieldGroup">
                    <label>加签密钥（可选）</label>
                    <input type="text" id="botSecret" value="${escapeHtml(secret)}" placeholder="SECxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx">
                    <small style="color: #6c757d; font-size: 0.8em;">钉钉机器人安全设置中开启「加签」后，复制此处密钥</small>
                </div>
                <div class="form-group">
                    <label>消息类型</label>
                    <div style="display: flex; gap: 15px; flex-wrap: wrap;">
                        <label style="display: flex; align-items: center; gap: 5px; font-weight: normal; cursor: pointer;">
                            <input type="checkbox" id="eventBaseStationOnline" value="base_station_online" ${events.includes('base_station_online') ? 'checked' : ''}>
                            基站上线
                        </label>
                        <label style="display: flex; align-items: center; gap: 5px; font-weight: normal; cursor: pointer;">
                            <input type="checkbox" id="eventBaseStationOffline" value="base_station_offline" ${events.includes('base_station_offline') ? 'checked' : ''}>
                            基站下线
                        </label>
                        <label style="display: flex; align-items: center; gap: 5px; font-weight: normal; cursor: pointer;">
                            <input type="checkbox" id="eventMountOnline" value="mount_online" ${events.includes('mount_online') ? 'checked' : ''}>
                            移动站上线
                        </label>
                        <label style="display: flex; align-items: center; gap: 5px; font-weight: normal; cursor: pointer;">
                            <input type="checkbox" id="eventMountOffline" value="mount_offline" ${events.includes('mount_offline') ? 'checked' : ''}>
                            移动站下线
                        </label>
                    </div>
                </div>
                <div class="form-group">
                    <label style="display: flex; align-items: center; gap: 5px; font-weight: normal; cursor: pointer;">
                        <input type="checkbox" id="botEnabled" ${enabled ? 'checked' : ''}>
                        启用该机器人
                    </label>
                </div>
                <div class="form-actions">
                    <button class="btn btn-secondary" onclick="closeModal('botModal')">取消</button>
                    <button class="btn btn-success" onclick="submitNotificationBot(${isEdit ? bot.id : 'null'})">${isEdit ? '保存' : '添加'}</button>
                </div>
            </div>
        </div>
    `;
    document.body.insertAdjacentHTML('beforeend', formHtml);
}

async function submitNotificationBot(botId) {
    const name = document.getElementById('botName').value.trim();
    const platform = document.getElementById('botPlatform').value;
    const webhookUrl = document.getElementById('botWebhookUrl').value.trim();
    const secret = document.getElementById('botSecret').value.trim();
    const enabled = document.getElementById('botEnabled').checked;
    const events = [];
    if (document.getElementById('eventBaseStationOnline').checked) events.push('base_station_online');
    if (document.getElementById('eventBaseStationOffline').checked) events.push('base_station_offline');
    if (document.getElementById('eventMountOnline').checked) events.push('mount_online');
    if (document.getElementById('eventMountOffline').checked) events.push('mount_offline');

    if (!name) {
        showAlert('机器人名称不能为空', 'error');
        return;
    }
    if (!webhookUrl) {
        showAlert('Webhook 地址不能为空', 'error');
        return;
    }
    if (events.length === 0) {
        showAlert('请至少选择一个消息类型', 'error');
        return;
    }

    const payload = { name, platform, webhook_url: webhookUrl, secret, enabled, events };
    const url = botId ? `/api/notification/bots/${botId}` : '/api/notification/bots';
    const method = botId ? 'PUT' : 'POST';

    try {
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await handleApiResponse(response);
        if (result.success) {
            showAlert(result.message, 'success');
            closeModal('botModal');
            loadNotificationBots();
        } else {
            showAlert('保存失败：' + (result.error || '未知错误'), 'error');
        }
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('保存失败：' + error.message, 'error');
        }
    }
}

async function editNotificationBot(botId) {
    try {
        const response = await fetch('/api/notification/bots');
        const result = await handleApiResponse(response);
        if (result.success && result.bots) {
            const bot = result.bots.find(b => b.id === botId);
            if (bot) {
                showNotificationBotModal(bot);
            }
        }
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('加载机器人信息失败：' + error.message, 'error');
        }
    }
}

function deleteNotificationBot(botId) {
    showConfirmDialog(
        '确认删除',
        '确定要删除该消息机器人配置吗？此操作无法撤销。',
        async () => {
            try {
                const response = await fetch(`/api/notification/bots/${botId}`, {
                    method: 'DELETE'
                });
                const result = await handleApiResponse(response);
                if (result.success) {
                    showAlert(result.message, 'success');
                    loadNotificationBots();
                } else {
                    showAlert('删除失败：' + (result.error || '未知错误'), 'error');
                }
            } catch (error) {
                if (error.message !== 'Unauthorized access') {
                    showAlert('删除失败：' + error.message, 'error');
                }
            }
        }
    );
}

async function loadAppInfo() {
    try {
        const response = await fetch('/api/app_info');
        if (response.ok) {
            const appInfo = await response.json();
            // Footer 元素已移除，此函数保留为空壳以避免破坏其他调用方
        }
    } catch (error) {
        // console.error('Failed to load application information:', error);
    }
}

// ==================== 连接事件日志功能 ====================

// 排序状态（每次进入页面由 getConnectionEventsContent 重置为默认）
let connectionEventsSort = { by: 'event_time', order: 'DESC' };
// 每页条数（每次进入页面由 getConnectionEventsContent 重置为 100）
let connectionEventsPageSize = 100;
// 各字段的默认排序方向
const CONNECTION_EVENTS_DEFAULT_ORDER = {
    event_time: 'DESC',
    event_type: 'ASC',
    mount_name: 'ASC',
    username: 'ASC',
    ip_address: 'ASC',
};
// 每页条数可选值
const CONNECTION_EVENTS_PAGE_SIZE_OPTIONS = [50, 100, 200, 500];

function getConnectionEventsContent() {
    // 每次进入页面，重置排序状态和每页条数
    connectionEventsSort = { by: 'event_time', order: 'DESC' };
    connectionEventsPageSize = 100;
    const pageSizeOptions = CONNECTION_EVENTS_PAGE_SIZE_OPTIONS
        .map(n => `<option value="${n}" ${n === 100 ? 'selected' : ''}>${n} 条/页</option>`)
        .join('');
    return `
        <div class="page-header">
            <h3>连接事件日志</h3>
            <p class="page-subtitle">基站（上传端）和移动站（用户连接/下载端）的上下线记录</p>
        </div>
        <div class="settings-container" style="max-width: 100%; margin: 0 auto;">
            <div class="settings-section">
                <div class="form-group" style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 0;">
                    <select id="connection-event-filter" class="form-control" style="width: auto; min-width: 120px;">
                        <option value="">全部事件</option>
                        <option value="base_station_online">基站上线</option>
                        <option value="base_station_offline">基站下线</option>
                        <option value="mount_online">移动站上线</option>
                        <option value="mount_offline">移动站下线</option>
                    </select>
                    <input type="text" id="connection-event-mount" placeholder="挂载点名称" class="form-control" style="width: auto; min-width: 120px;">
                    <input type="text" id="connection-event-user" placeholder="用户名" class="form-control" style="width: auto; min-width: 120px;">
                    <input type="datetime-local" id="connection-event-start" class="form-control" style="width: auto; min-width: 160px;">
                    <input type="datetime-local" id="connection-event-end" class="form-control" style="width: auto; min-width: 160px;">
                    <button onclick="loadConnectionEvents(0, connectionEventsPageSize)" class="btn btn-primary">查询</button>
                    <select id="connection-events-page-size" class="form-control" style="width: auto; min-width: 110px;" onchange="changeConnectionEventsPageSize(this.value)">
                        ${pageSizeOptions}
                    </select>
                </div>
            </div>
            <div id="connection-events-list">
                <p class="loading-text">正在加载事件日志...</p>
            </div>
        </div>
    `;
}

function changeConnectionEventsPageSize(newSize) {
    const n = parseInt(newSize, 10);
    if (!Number.isFinite(n) || n <= 0) return;
    connectionEventsPageSize = n;
    loadConnectionEvents(0, n);
}

async function loadConnectionEvents(offset = 0, limit = 100) {
    try {
        const eventType = document.getElementById('connection-event-filter')?.value || '';
        const mountName = document.getElementById('connection-event-mount')?.value.trim() || '';
        const username = document.getElementById('connection-event-user')?.value.trim() || '';
        const startInput = document.getElementById('connection-event-start')?.value || '';
        const endInput = document.getElementById('connection-event-end')?.value || '';
        const startTime = startInput ? startInput.replace('T', ' ') + ':00' : '';
        const endTime = endInput ? endInput.replace('T', ' ') + ':00' : '';

        const params = new URLSearchParams();
        if (eventType) params.append('event_type', eventType);
        if (mountName) params.append('mount_name', mountName);
        if (username) params.append('username', username);
        if (startTime) params.append('start_time', startTime);
        if (endTime) params.append('end_time', endTime);
        params.append('limit', String(limit));
        params.append('offset', String(offset));
        params.append('sort_by', connectionEventsSort.by);
        params.append('sort_order', connectionEventsSort.order);

        const response = await fetch('/api/connection_events?' + params.toString());
        const result = await handleApiResponse(response);
        if (result.success) {
            if (result.sort_by) connectionEventsSort.by = result.sort_by;
            if (result.sort_order) connectionEventsSort.order = result.sort_order;
            renderConnectionEvents(result.events || [], result.statistics || {}, {
                offset: result.offset,
                limit: result.limit,
                total: result.total
            });
        } else {
            showAlert('加载连接事件日志失败：' + (result.error || '未知错误'), 'error');
        }
    } catch (error) {
        if (error.message !== 'Unauthorized access') {
            showAlert('加载连接事件日志失败：' + error.message, 'error');
        }
    }
}

// 点击列头：相同列切换方向，不同列使用该字段默认方向
function sortConnectionEvents(column) {
    if (connectionEventsSort.by === column) {
        connectionEventsSort.order = connectionEventsSort.order === 'ASC' ? 'DESC' : 'ASC';
    } else {
        connectionEventsSort.by = column;
        connectionEventsSort.order = CONNECTION_EVENTS_DEFAULT_ORDER[column] || 'ASC';
    }
    loadConnectionEvents(0, 100);
}

// 生成排序方向指示器（连接事件）
function sortIndicatorConnectionEvents(column) {
    if (connectionEventsSort.by !== column) return '<span style="color: #adb5bd; font-size: 0.85em;">⇅</span>';
    const arrow = connectionEventsSort.order === 'ASC' ? '↑' : '↓';
    return `<span style="color: #007bff; font-weight: bold;">${arrow}</span>`;
}

function renderConnectionEvents(events, statistics, pagination = null) {
    const container = document.getElementById('connection-events-list');
    if (!container) return;

    const eventTypeColors = {
        'base_station_online': '#28a745',
        'base_station_offline': '#dc3545',
        'mount_online': '#17a2b8',
        'mount_offline': '#fd7e14'
    };

    const eventLabels = {
        'base_station_online': '基站上线',
        'base_station_offline': '基站下线',
        'mount_online': '移动站上线',
        'mount_offline': '移动站下线'
    };

    const statItems = Object.entries(statistics).map(([type, count]) => {
        return `<span style="display: inline-block; margin-right: 15px; font-size: 0.85em; color: #6c757d;">${eventLabels[type] || type}: <strong style="color: ${eventTypeColors[type] || '#333'};">${count}</strong></span>`;
    }).join('');

    let paginationHtml = '';
    if (pagination) {
        const { offset, limit, total } = pagination;
        const currentPage = Math.floor(offset / limit) + 1;
        const totalPages = Math.max(1, Math.ceil(total / limit));
        const hasPrev = offset > 0;
        const hasNext = offset + events.length < total;
        const firstOffset = 0;
        const lastOffset = Math.max(0, (totalPages - 1) * limit);
        const startRange = total > 0 ? offset + 1 : 0;
        const endRange = offset + events.length;

        paginationHtml = `
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 10px; font-size: 0.85em; color: #6c757d;">
                <div>显示第 ${startRange} - ${endRange} 条，共 ${total} 条</div>
                <div style="display: flex; gap: 5px; align-items: center; flex-wrap: wrap;">
                    <button class="btn btn-secondary btn-sm" ${!hasPrev ? 'disabled' : ''} onclick="loadConnectionEvents(${firstOffset}, ${limit})" title="首页">« 首页</button>
                    <button class="btn btn-secondary btn-sm" ${!hasPrev ? 'disabled' : ''} onclick="loadConnectionEvents(${offset - limit}, ${limit})" title="上一页">上一页</button>
                    <span style="display: inline-flex; align-items: center; gap: 4px;">
                        第
                        <input type="number" id="connection-events-page-input" min="1" max="${totalPages}" value="${currentPage}"
                               style="width: 60px; padding: 2px 6px; font-size: 0.95em; text-align: center; border: 1px solid #ced4da; border-radius: 3px;"
                               onkeydown="if(event.key === 'Enter'){ event.preventDefault(); jumpConnectionEventsPage(${limit}); }" />
                        / ${totalPages} 页
                    </span>
                    <button class="btn btn-primary btn-sm" onclick="jumpConnectionEventsPage(${limit})" title="跳转到指定页">跳转</button>
                    <button class="btn btn-secondary btn-sm" ${!hasNext ? 'disabled' : ''} onclick="loadConnectionEvents(${offset + limit}, ${limit})" title="下一页">下一页</button>
                    <button class="btn btn-secondary btn-sm" ${!hasNext ? 'disabled' : ''} onclick="loadConnectionEvents(${lastOffset}, ${limit})" title="尾页">尾页 »</button>
                </div>
            </div>
        `;
    }

    if (!events || events.length === 0) {
        container.innerHTML = `
            <div style="margin-bottom: 10px;">${statItems || '<span style="font-size: 0.85em; color: #6c757d;">暂无统计</span>'}</div>
            <p class="empty-text" style="color: #6c757d; padding: 10px 0;">暂无连接事件日志。</p>
            ${paginationHtml}
        `;
        return;
    }

    const rows = events.map(e => {
        const color = eventTypeColors[e.event_type] || '#6c757d';
        const label = e.event_label || eventLabels[e.event_type] || e.event_type;
        const mountCell = e.mount_name ? `<td>${escapeHtml(e.mount_name)}</td>` : '<td>-</td>';
        const userCell = e.username ? `<td>${escapeHtml(e.username)}</td>` : '<td>-</td>';
        const ipCell = e.ip_address ? `<td>${escapeHtml(e.ip_address)}</td>` : '<td>-</td>';
        const durationCell = e.duration ? `<td>${e.duration} 秒</td>` : '<td>-</td>';
        const reasonCell = e.reason ? `<td>${escapeHtml(e.reason)}</td>` : '<td>-</td>';
        return `
            <tr>
                <td style="color: ${color}; font-weight: bold;">${label}</td>
                ${mountCell}
                ${userCell}
                ${ipCell}
                <td>${escapeHtml(e.event_time)}</td>
                ${durationCell}
                ${reasonCell}
            </tr>
        `;
    }).join('');

    const html = `
        <div style="margin-bottom: 10px;">${statItems || '<span style="font-size: 0.85em; color: #6c757d;">暂无统计</span>'}</div>
        <div class="table-container" style="max-height: 800px; overflow-y: auto;">
            <table class="data-table" style="min-width: 600px;">
                <thead style="position: sticky; top: 0; z-index: 1; background: #f8f9fa;">
                    <tr>
                        <th style="cursor: pointer; user-select: none;" onclick="sortConnectionEvents('event_type')" title="点击按事件类型排序">事件类型 ${sortIndicatorConnectionEvents('event_type')}</th>
                        <th style="cursor: pointer; user-select: none;" onclick="sortConnectionEvents('mount_name')" title="点击按挂载点排序">挂载点 ${sortIndicatorConnectionEvents('mount_name')}</th>
                        <th style="cursor: pointer; user-select: none;" onclick="sortConnectionEvents('username')" title="点击按用户名排序">用户名 ${sortIndicatorConnectionEvents('username')}</th>
                        <th style="cursor: pointer; user-select: none;" onclick="sortConnectionEvents('ip_address')" title="点击按 IP 地址排序">IP 地址 ${sortIndicatorConnectionEvents('ip_address')}</th>
                        <th style="cursor: pointer; user-select: none;" onclick="sortConnectionEvents('event_time')" title="点击按时间排序">时间 ${sortIndicatorConnectionEvents('event_time')}</th>
                        <th>持续时长</th>
                        <th>原因</th>
                    </tr>
                </thead>
                <tbody>
                    ${rows}
                </tbody>
            </table>
        </div>
        ${paginationHtml}
    `;

    container.innerHTML = html;
}

function jumpConnectionEventsPage(limit = 100) {
    const input = document.getElementById('connection-events-page-input');
    if (!input) return;
    let page = parseInt(input.value, 10);
    if (!Number.isFinite(page) || page < 1) {
        page = 1;
        input.value = 1;
    }
    const maxPage = parseInt(input.max, 10);
    if (Number.isFinite(maxPage) && maxPage > 0 && page > maxPage) {
        page = maxPage;
        input.value = maxPage;
    }
    const offset = (page - 1) * limit;
    loadConnectionEvents(offset, limit);
}


