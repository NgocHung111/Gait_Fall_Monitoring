// Khởi tạo bản đồ Leaflet trung tâm TP.HCM
const map = L.map('map').setView([10.762622, 106.660172], 15);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors'
}).addTo(map);

// Hàm đánh dấu vị trí té ngã lên bản đồ
function markFallLocation(lat, lng, timeStr) {
    const marker = L.marker([lat, lng]).addTo(map);
    marker.bindPopup(`<b>Té ngã phát hiện!</b><br>Thời gian: ${timeStr}`).openPopup();
    map.panTo([lat, lng]);
}