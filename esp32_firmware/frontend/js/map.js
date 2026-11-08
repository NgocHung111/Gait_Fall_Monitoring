(function () {
  "use strict";

  const markerLayer = window.L ? window.L.layerGroup() : null;
  let map;
  let latestCoordinates = null;

  function initializeMap() {
    const container = document.getElementById("fall-map");
    if (!container || !window.L) {
      return;
    }

    map = window.L.map(container, {
      scrollWheelZoom: false,
      zoomControl: true,
    }).setView([16.0, 106.0], 5);

    window.L.tileLayer(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      {
        maxZoom: 19,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }
    ).addTo(map);
    markerLayer.addTo(map);
  }

  function renderLocations(records) {
    if (!map || !markerLayer) {
      return;
    }

    markerLayer.clearLayers();
    const points = [];
    const fallRecords = records.filter(function (record) {
      return record && record.event === "FALL_DETECTED";
    });
    records.forEach(function (record) {
      const location = record && record.location;
      if (!location || !Number.isFinite(location.lat) || !Number.isFinite(location.lng)) {
        return;
      }

      const point = [location.lat, location.lng];
      points.push(point);
      const occurredAt = record.timestamp_utc
        ? new Date(record.timestamp_utc).toLocaleString("vi-VN")
        : "Thời điểm không xác định";
      const sourceLabel =
        record.location_source === "phone"
          ? "GPS điện thoại"
          : record.location_source === "wifi"
            ? "Định vị Wi-Fi"
            : "Vị trí";
      window.L.marker(point)
        .bindPopup(
          "<strong>Phát hiện té ngã</strong><br>" +
            occurredAt +
            "<br>" +
            sourceLabel
        )
        .addTo(markerLayer);
    });

    const emptyState = document.getElementById("map-empty");
    const caption = document.getElementById("location-caption");
    const openMapLink = document.getElementById("open-map-link");
    const status = document.getElementById("fall-status");
    if (points.length === 0) {
      latestCoordinates = null;
      emptyState.hidden = false;
      if (fallRecords.length > 0) {
        const latestFall = fallRecords[fallRecords.length - 1];
        emptyState.querySelector("strong").textContent =
          "Đã phát hiện té ngã nhưng chưa có tọa độ";
        emptyState.querySelector("span:last-child").textContent =
          latestFall.location_error || "Backend chưa trả về vị trí.";
        caption.textContent = "Không xác định được vị trí sự kiện gần nhất";
        status.classList.add("alert");
        status.innerHTML =
          "<i></i> Có " + fallRecords.length + " sự kiện té ngã";
      } else {
        emptyState.querySelector("strong").textContent = "Chưa có vị trí té ngã";
        emptyState.querySelector("span:last-child").textContent =
          "Vị trí sẽ xuất hiện khi điện thoại chia sẻ GPS hoặc backend xác định được tọa độ.";
        caption.textContent = "Đang chờ dữ liệu vị trí";
        status.classList.remove("alert");
        status.innerHTML = "<i></i> Chưa có sự kiện";
      }
      openMapLink.hidden = true;
      return;
    }

    latestCoordinates = points[points.length - 1];
    emptyState.hidden = true;
    map.fitBounds(points, { padding: [30, 30], maxZoom: 16 });
    const lat = latestCoordinates[0];
    const lng = latestCoordinates[1];
    caption.textContent =
      "Tọa độ gần nhất: " + lat.toFixed(5) + ", " + lng.toFixed(5);
    openMapLink.href = "https://maps.google.com/?q=" + lat + "," + lng;
    openMapLink.hidden = false;
    status.classList.add("alert");
    status.innerHTML =
      "<i></i> Có " + fallRecords.length + " sự kiện té ngã";
  }

  function focusLatestLocation() {
    if (map && latestCoordinates) {
      map.setView(latestCoordinates, 16);
    }
  }

  window.FallMap = {
    initialize: initializeMap,
    renderLocations: renderLocations,
    focusLatestLocation: focusLatestLocation,
  };
})();
