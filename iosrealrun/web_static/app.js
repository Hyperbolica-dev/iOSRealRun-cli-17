(() => {
  const points = [];
  const markers = [];
  const map = L.map('map').setView([30.53, 120.73], 13);
  const line = L.polyline([], { color: '#1677ff', weight: 4 }).addTo(map);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors', maxZoom: 19
  }).addTo(map);

  const $ = (id) => document.getElementById(id);
  const message = (text) => { $('message').textContent = text || ''; $('error').textContent = ''; };
  const error = (text) => { $('error').textContent = text || ''; $('message').textContent = ''; };
  const stateNames = { connecting: '正在连接', running: '运行中', stopping: '正在停止', idle: '空闲', error: '错误' };

  function updateRoute() {
    line.setLatLngs(points.map((p) => [p.lat, p.lng]));
    $('point-count').textContent = points.length;
    let distance = 0;
    for (let i = 1; i < points.length; i += 1) {
      distance += map.distance([points[i - 1].lat, points[i - 1].lng], [points[i].lat, points[i].lng]);
    }
    $('distance').textContent = distance >= 1000 ? `${(distance / 1000).toFixed(2)} 千米` : `${Math.round(distance)} 米`;
  }

  function addPoint(lat, lng) {
    const point = { lat: Number(lat), lng: Number(lng) };
    points.push(point);
    const marker = L.marker([point.lat, point.lng], { draggable: true }).addTo(map);
    marker.on('dragend', () => {
      const position = marker.getLatLng();
      point.lat = position.lat;
      point.lng = position.lng;
      updateRoute();
    });
    markers.push(marker);
    updateRoute();
  }

  function clearRoute() {
    markers.splice(0).forEach((marker) => marker.remove());
    points.splice(0);
    updateRoute();
  }

  function loadPoints(routePoints) {
    clearRoute();
    routePoints.forEach((point) => addPoint(point.lat, point.lng));
    if (points.length) $('fit').click();
  }

  map.on('click', (event) => addPoint(event.latlng.lat, event.latlng.lng));
  $('undo').onclick = () => { const marker = markers.pop(); if (marker) marker.remove(); points.pop(); updateRoute(); };
  $('clear').onclick = clearRoute;
  $('fit').onclick = () => { if (points.length) map.fitBounds(line.getBounds(), { padding: [20, 20] }); };

  async function request(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  async function refreshDevices() {
    try {
      const devices = await request('/api/devices');
      const select = $('device-select');
      const previous = select.value;
      select.innerHTML = '<option value="">选择设备</option>';
      devices.forEach((device) => {
        const option = document.createElement('option');
        option.value = device.udid;
        option.textContent = device.name ? `${device.name} (${device.udid})` : device.udid;
        option.dataset.version = device.version || '—';
        option.dataset.developerMode = device.developer_mode == null ? '—' : (device.developer_mode ? '已开启' : '未开启');
        select.appendChild(option);
      });
      if (devices.length === 1) select.value = devices[0].udid;
      if (devices.some((device) => device.udid === previous)) select.value = previous;
      updateDeviceInfo();
      message(`已发现 ${devices.length} 台设备`);
    } catch (requestError) { error(requestError.message); }
  }

  function updateDeviceInfo() {
    const selected = $('device-select').selectedOptions[0];
    $('device-info').textContent = selected && selected.value ? selected.textContent : '尚未选择设备';
    $('device-status').textContent = selected && selected.value
      ? `系统版本：${selected.dataset.version} · Developer Mode：${selected.dataset.developerMode}`
      : '系统版本：— · Developer Mode：—';
  }
  $('device-select').onchange = updateDeviceInfo;
  $('refresh-devices').onclick = refreshDevices;

  async function refreshRoutes() {
    const names = await request('/api/routes');
    const select = $('route-select');
    select.innerHTML = '<option value="">选择已保存路线</option>';
    names.forEach((name) => { const option = document.createElement('option'); option.value = name; option.textContent = name; select.appendChild(option); });
  }
  $('load').onclick = async () => {
    if (!$('route-select').value) return;
    try { loadPoints((await request(`/api/routes/${encodeURIComponent($('route-select').value)}`)).points); message('路线已加载'); } catch (requestError) { error(requestError.message); }
  };
  $('load-default').onclick = async () => {
    try { loadPoints((await request('/api/routes/default')).points); message('默认路线已加载'); } catch (requestError) { error(requestError.message); }
  };
  $('save').onclick = async () => {
    const name = window.prompt('路线名称（字母、数字、.、_、-）：');
    if (!name) return;
    try {
      const result = await request(`/api/routes/${encodeURIComponent(name)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ points }) });
      await refreshRoutes(); $('route-select').value = result.name; message(`已保存 ${result.name}`);
    } catch (requestError) { error(requestError.message); }
  };
  $('delete').onclick = async () => {
    const name = $('route-select').value;
    if (!name || !window.confirm(`确定删除 ${name}？`)) return;
    try { await request(`/api/routes/${encodeURIComponent(name)}`, { method: 'DELETE' }); await refreshRoutes(); message(`已删除 ${name}`); } catch (requestError) { error(requestError.message); }
  };

  $('start').onclick = async () => {
    if (!$('device-select').value) return error('请先选择设备');
    try {
      await request('/api/simulation/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ udid: $('device-select').value, points, speed: Number($('speed').value) }) });
      message('模拟正在连接');
    } catch (requestError) { error(requestError.message); }
  };
  $('stop').onclick = async () => {
    try { await request('/api/simulation/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ udid: $('device-select').value }) }); message('模拟已停止，正在清理位置'); } catch (requestError) { error(requestError.message); }
  };

  $('diagnose').onclick = async () => {
    if (!points.length) return error('请先在地图上添加至少一个点');
    try {
      const result = await request('/api/diagnostic/coordinates', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ point: points[0] })
      });
      $('coordinate-diagnostic').textContent = [
        `Leaflet 点击：${JSON.stringify(result.clicked)}`,
        `API 提交：${JSON.stringify(result.submitted)}`,
        `路线存储：${JSON.stringify(result.stored)}`,
        `LocationSimulation.set：${JSON.stringify(result.location_simulation_set)}`
      ].join('\n');
      message('坐标诊断完成');
    } catch (requestError) { error(requestError.message); }
  };

  async function pollStatus() {
    try {
      const udid = $('device-select').value;
      const status = await request(`/api/status${udid ? `?udid=${encodeURIComponent(udid)}` : ''}`);
      $('state').textContent = stateNames[status.state] || status.state || '空闲';
      $('backend').textContent = `连接方式：${status.backend || '—'}`;
      $('progress').textContent = `循环次数：${status.loop_count || 0} · 当前点：${status.current_index || '—'} · 已运行时间：${Math.round(status.elapsed_seconds || 0)} 秒`;
      const active = ['connecting', 'running', 'stopping'].includes(status.state);
      $('start').disabled = active;
      $('stop').disabled = !active;
      if (status.last_error) error(status.last_error);
    } catch (requestError) { error(requestError.message); }
  }

  refreshDevices();
  refreshRoutes().catch((requestError) => error(requestError.message));
  setInterval(pollStatus, 1000);
  pollStatus();
})();
