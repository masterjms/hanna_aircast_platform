/**
 * 카카오맵 래퍼 — 마커 그리기만 한다.
 *
 * 지도 설계 §4.6 의 경계선: 이 컴포넌트는 API·폴링·권한을 모른다. 받은 배열을
 * 그릴 뿐이다. 목록과의 연동도 부모의 selectedMac/hoveredMac 두 상태로만 만난다.
 *
 * 마커는 표준 핀 모양(SVG 를 MarkerImage 로)이고 상태색만 다르다 —
 * 온라인=초록, 무음(RECONNECTING)=주황, 오프라인=빨강, 위치 미입력(마을 좌표
 * fallback)=반투명. 이름은 항상 그리지 않고 **호버/선택 시 툴팁**으로 보여준다.
 * 폴링마다 마커를 재생성하지 않고 mac 별로 재사용한다(§4.4).
 *
 * 마을 경계(§4.8)도 여기서 그린다. 마커 아래 깔리는 배경이라 z-index 를 낮게 두고
 * 리스너를 달지 않는다 — 폴리곤이 커서를 가로채면 마커 호버가 다시 깨진다.
 */

import { useEffect, useRef, useState } from 'react';

import { loadKakaoMaps } from '../lib/kakao';
import type { GeoGeometry, MapPin, MapVillage } from '../api/types';

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Entry {
  marker: any;
  pin: MapPin;
  imageKey: string;
  /** 마지막으로 준 z-index. 같은 값을 다시 주지 않으려고 기억한다. */
  zIndex: number;
}

/** 마을 경계 폴리곤 한 채. 마을 id 로 재사용한다. */
interface BoundaryEntry {
  polygons: any[];
  /** 마지막으로 그린 도형. 같은 것이면 다시 만들지 않는다(폴링마다 온다). */
  signature: string;
}

/**
 * 마커를 벗어난 뒤 이름을 지우기까지의 유예(ms).
 *
 * 마커 위에 마우스를 두고 가만히 있어도 mouseout 이 튀는 순간이 있다 — 지도가
 * 다시 그려지거나 오버레이가 커서 아래를 스칠 때다. 그때마다 곧바로 지우면
 * mouseout → 이름 사라짐 → mouseover → 이름 나타남이 반복되어 빠르게 깜빡인다.
 * 짧게 유예하고 그 사이에 다시 들어오면 없던 일로 한다.
 */
const HOVER_CLEAR_DELAY_MS = 140;

/**
 * 이 확대 수준 이상(=더 멀리서 볼 때)이면 마커를 묶는다. 카카오 level 은 1이 가장
 * 가깝고 14가 전국이다. 5 는 마을 하나가 화면에 한 덩어리로 보이는 축척쯤이라,
 * 그보다 멀면 마을 단위 숫자로, 들어오면 개별 핀으로 보인다.
 */
const CLUSTER_MIN_LEVEL = 5;
/** 단말을 선택하면 이 수준까지 확대한다. 건물과 골목이 보이는 축척이다. */
const SELECT_LEVEL = 3;

const PIN_W = 28;
const PIN_H = 40;
//: 선택된 핀은 한 단계 크게 — 색만으로는 어느 것을 골랐는지 안 보인다.
const SELECTED_SCALE = 1.3;

function statusColor(pin: MapPin): string {
  if (!pin.online) return '#d9534f';
  if (pin.live === 'RECONNECTING') return '#d99a2b';
  return '#2f9e63';
}

/** 표준 물방울 핀. 이미지 파일 대신 SVG data URI — 색·투명도를 코드로 만든다. */
function pinDataUri(color: string, opacity: number): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${PIN_W}" height="${PIN_H}" viewBox="0 0 28 40">` +
    `<path d="M14 1C6.8 1 1 6.8 1 14c0 9.7 13 25 13 25s13-15.3 13-25C27 6.8 21.2 1 14 1z"` +
    ` fill="${color}" fill-opacity="${opacity}" stroke="#ffffff" stroke-width="1.5"/>` +
    `<circle cx="14" cy="14" r="4.5" fill="#ffffff" fill-opacity="${Math.min(1, opacity + 0.2)}"/>` +
    `</svg>`;
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

/**
 * GeoJSON geometry → 폴리곤별 고리 목록.
 *
 * Polygon 은 [바깥 고리, 구멍…] 하나, MultiPolygon 은 그게 여러 개다(섬).
 * 그래서 반환값은 [폴리곤][고리][점][경도·위도] 네 겹이다.
 * 좌표는 GeoJSON 규약대로 [경도, 위도] 순서다 — 카카오 LatLng 과 반대라
 * 넘길 때 뒤집는다.
 */
function ringsOf(geometry: GeoGeometry): number[][][][] {
  return geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
}

/** 같은 도형인지 싸게 판별하는 지문. 좌표 전체를 비교하지 않는다. */
function boundarySignature(geometry: GeoGeometry): string {
  const shape = ringsOf(geometry)
    .map((rings) => rings.map((ring) => ring.length).join(','))
    .join('|');
  return `${geometry.type}:${shape}`;
}

export function MapView({
  jsKey,
  pins,
  villages,
  selectedMac,
  hoveredMac,
  onSelect,
  onHover,
}: {
  jsKey: string;
  pins: MapPin[];
  /** 경계를 그릴 마을. boundary 가 null 인 마을은 건너뛴다. */
  villages: MapVillage[];
  selectedMac: string | null;
  hoveredMac: string | null;
  onSelect: (mac: string | null) => void;
  onHover: (mac: string | null) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const mapsRef = useRef<any>(null);
  const entriesRef = useRef<Map<string, Entry>>(new Map());
  const imageCacheRef = useRef<Map<string, any>>(new Map());
  const tooltipRef = useRef<any>(null);
  const tooltipElRef = useRef<HTMLDivElement | null>(null);
  /** 지금 화면에 떠 있는 툴팁 내용. 같은 값이면 다시 그리지 않는다(깜빡임 방지). */
  const shownTooltipRef = useRef<{ mac: string; text: string; lat: number; lng: number } | null>(
    null,
  );
  const boundariesRef = useRef<Map<number, BoundaryEntry>>(new Map());
  const clustererRef = useRef<any>(null);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const fittedRef = useRef(false);
  const [sdkError, setSdkError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // 콜백을 ref 로 — 마커 리스너가 최신 핸들러를 보게 하면서 재부착은 피한다.
  const onSelectRef = useRef(onSelect);
  const onHoverRef = useRef(onHover);
  onSelectRef.current = onSelect;
  onHoverRef.current = onHover;

  /** 마커를 벗어났다고 알리기 전에 잠깐 기다리는 타이머. */
  const hoverClearRef = useRef<number | null>(null);

  /**
   * 마커 호버 전달. 들어올 때는 즉시, 나갈 때는 유예를 두고 알린다
   * (위 HOVER_CLEAR_DELAY_MS 주석 참고 — 이름이 깜빡이는 것을 막는다).
   */
  const emitHover = (mac: string | null) => {
    if (hoverClearRef.current !== null) {
      window.clearTimeout(hoverClearRef.current);
      hoverClearRef.current = null;
    }
    if (mac !== null) {
      onHoverRef.current(mac);
      return;
    }
    hoverClearRef.current = window.setTimeout(() => {
      hoverClearRef.current = null;
      onHoverRef.current(null);
    }, HOVER_CLEAR_DELAY_MS);
  };

  useEffect(
    () => () => {
      if (hoverClearRef.current !== null) window.clearTimeout(hoverClearRef.current);
    },
    [],
  );

  /** 상태·투명도·크기별 MarkerImage. 같은 조합은 캐시해서 재사용한다. */
  const markerImage = (pin: MapPin, selected: boolean) => {
    const maps = mapsRef.current;
    const color = statusColor(pin);
    const opacity = pin.position_source === 'device' ? 1 : 0.55;
    const scale = selected ? SELECTED_SCALE : 1;
    const key = `${color}|${opacity}|${scale}`;
    let img = imageCacheRef.current.get(key);
    if (!img) {
      const w = Math.round(PIN_W * scale);
      const h = Math.round(PIN_H * scale);
      img = new maps.MarkerImage(pinDataUri(color, opacity), new maps.Size(w, h), {
        // 핀 끝이 좌표를 가리키게 — 바닥 중앙 기준.
        offset: new maps.Point(w / 2, h),
      });
      imageCacheRef.current.set(key, img);
    }
    return { img, key };
  };

  // 지도 생성 — 1회.
  useEffect(() => {
    let alive = true;
    loadKakaoMaps(jsKey)
      .then((maps) => {
        if (!alive || !containerRef.current) return;
        mapsRef.current = maps;
        mapRef.current = new maps.Map(containerRef.current, {
          // 한반도 전체가 보이는 기본값. 핀이 오면 setBounds 로 좁힌다.
          center: new maps.LatLng(36.2, 127.8),
          level: 13,
        });
        // 빈 곳 클릭 = 선택 해제.
        maps.event.addListener(mapRef.current, 'click', () => onSelectRef.current(null));
        // 줌 ± 버튼. 휠·더블클릭도 되지만 태블릿·마우스 없는 환경에서는 이게 유일하다.
        mapRef.current.addControl(new maps.ZoomControl(), maps.ControlPosition.RIGHT);
        // 마커 클러스터러 — 확대 수준이 CLUSTER_MIN_LEVEL 이상(멀리서 볼 때)이면
        // 가까운 핀을 숫자 하나로 묶는다. 마을 안으로 들어가면 개별 핀이 된다.
        clustererRef.current = new maps.MarkerClusterer({
          map: mapRef.current,
          averageCenter: true,
          minLevel: CLUSTER_MIN_LEVEL,
          // 클릭하면 SDK 가 한 단계 확대한다 — 묶인 것을 풀어 보는 자연스러운 동작.
          disableClickZoom: false,
          styles: [
            {
              width: '40px',
              height: '40px',
              borderRadius: '20px',
              background: 'rgba(74, 144, 217, 0.85)',
              border: '2px solid #ffffff',
              color: '#ffffff',
              textAlign: 'center',
              lineHeight: '36px',
              fontSize: '13px',
              fontWeight: '700',
              boxShadow: '0 2px 8px rgba(0,0,0,0.35)',
            },
          ],
        });
        // 이름 툴팁 — 호버/선택된 마커 위에 하나만 띄워 재사용한다.
        const el = document.createElement('div');
        el.className = 'map-tooltip';
        tooltipElRef.current = el;
        // yAnchor 1 = 오버레이 아래끝이 좌표(핀 끝)에 온다. 거기서 핀 높이만큼
        // 더 올리는 것은 CSS 의 transform 이 한다 — 오버레이 높이에 비례하는
        // yAnchor 로는 "핀보다 위"를 안정적으로 맞출 수 없다(글자 길이에 따라
        // 높이가 달라진다). 예전 값(1.15)은 핀 몸통과 겹쳐서, 커서가 툴팁에
        // 가려지며 mouseout 이 튀는 원인이었다.
        tooltipRef.current = new maps.CustomOverlay({
          content: el,
          yAnchor: 1,
          zIndex: 40,
          clickable: false,
        });
        // flex 레이아웃이 자리를 잡거나 창 크기가 바뀌면 relayout — 안 하면
        // 지도 일부가 타일을 안 받아온 회색으로 남는다.
        const observer = new ResizeObserver(() => mapRef.current?.relayout());
        observer.observe(containerRef.current);
        resizeObserverRef.current = observer;
        setReady(true);
      })
      .catch((e) => alive && setSdkError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
      resizeObserverRef.current?.disconnect();
      resizeObserverRef.current = null;
    };
  }, [jsKey]);

  // 마을 경계 동기화 — 마을 id 별 재사용, 사라진 것만 제거.
  //
  // 마커보다 먼저 그려서 아래에 깔리게 한다(zIndex 0). 리스너는 달지 않는다 —
  // 폴리곤이 커서를 받으면 그 위의 마커에 mouseout 이 걸려 이름이 깜빡인다(§4.7).
  useEffect(() => {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!ready || !maps || !map) return;

    const seen = new Set<number>();
    for (const village of villages) {
      if (!village.boundary) continue;
      seen.add(village.id);
      // 폴링마다 같은 도형이 다시 온다. 좌표를 통째로 비교하면 비싸니 형태와
      // 점 수만으로 지문을 만든다 — 경계는 사람이 다시 넣기 전에는 안 바뀐다.
      const signature = boundarySignature(village.boundary);
      const existing = boundariesRef.current.get(village.id);
      if (existing) {
        if (existing.signature === signature) continue;
        for (const polygon of existing.polygons) polygon.setMap(null);
      }
      const polygons = ringsOf(village.boundary).map(
        (rings) =>
          new maps.Polygon({
            // 바깥 고리 + 구멍. 카카오는 경로 배열의 배열로 구멍을 표현한다.
            path: rings.map((ring) => ring.map(([lng, lat]) => new maps.LatLng(lat, lng))),
            strokeWeight: 2,
            strokeColor: '#4a90d9',
            strokeOpacity: 0.8,
            strokeStyle: 'solid',
            fillColor: '#4a90d9',
            fillOpacity: 0.12,
            zIndex: 0,
            map,
          }),
      );
      boundariesRef.current.set(village.id, { polygons, signature });
    }

    for (const [id, entry] of boundariesRef.current) {
      if (seen.has(id)) continue;
      for (const polygon of entry.polygons) polygon.setMap(null);
      boundariesRef.current.delete(id);
    }
  }, [ready, villages]);

  // 마커 동기화 — mac 별 재사용, 사라진 것만 제거.
  useEffect(() => {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!ready || !maps || !map) return;

    const seen = new Set<string>();
    // 마커가 생기거나 움직였으면 클러스터를 다시 계산해야 한다.
    let clusterDirty = false;
    for (const pin of pins) {
      seen.add(pin.mac);
      const selected = pin.mac === selectedMac;
      const hovered = pin.mac === hoveredMac;
      let entry = entriesRef.current.get(pin.mac);
      if (!entry) {
        const { img, key } = markerImage(pin, selected);
        // map 을 직접 주지 않는다 — 클러스터러가 확대 수준에 따라 붙이고 떼어낸다.
        const marker = new maps.Marker({
          position: new maps.LatLng(pin.lat, pin.lng),
          image: img,
        });
        maps.event.addListener(marker, 'click', () => onSelectRef.current(pin.mac));
        maps.event.addListener(marker, 'mouseover', () => emitHover(pin.mac));
        maps.event.addListener(marker, 'mouseout', () => emitHover(null));
        entry = { marker, pin, imageKey: key, zIndex: 1 };
        entriesRef.current.set(pin.mac, entry);
        clustererRef.current?.addMarker(marker);
        clusterDirty = true;
      } else {
        if (entry.pin.lat !== pin.lat || entry.pin.lng !== pin.lng) {
          entry.marker.setPosition(new maps.LatLng(pin.lat, pin.lng));
          // 클러스터러는 위치 변경을 스스로 알지 못한다 — 아래에서 다시 그리게 표시.
          clusterDirty = true;
        }
        const { img, key } = markerImage(pin, selected);
        // setImage 는 이미지가 실제로 바뀔 때만 — 폴링마다 부르면 깜빡인다.
        if (key !== entry.imageKey) {
          entry.marker.setImage(img);
          entry.imageKey = key;
        }
      }
      entry.pin = pin;
      // 값이 그대로면 건드리지 않는다 — 호버할 때마다 전 마커를 다시 만지면
      // 커서 아래에서 마커가 다시 그려져 mouseout 이 튄다.
      const z = selected ? 30 : hovered ? 20 : 1;
      if (entry.zIndex !== z) {
        entry.zIndex = z;
        entry.marker.setZIndex(z);
      }
    }
    for (const [mac, entry] of entriesRef.current) {
      if (!seen.has(mac)) {
        clustererRef.current?.removeMarker(entry.marker);
        entry.marker.setMap(null);
        entriesRef.current.delete(mac);
        clusterDirty = true;
      }
    }
    if (clusterDirty) clustererRef.current?.redraw();

    // 첫 데이터에서 한 번만 전체 핀이 보이게 맞춘다 — 폴링마다 하면 지도가 널뛴다.
    if (!fittedRef.current && pins.length > 0) {
      fittedRef.current = true;
      map.relayout();
      const bounds = new maps.LatLngBounds();
      for (const pin of pins) bounds.extend(new maps.LatLng(pin.lat, pin.lng));
      map.setBounds(bounds);
      // flex 레이아웃이 완전히 자리 잡은 뒤 범위를 한 번 더 맞춘다.
      setTimeout(() => {
        map.relayout();
        map.setBounds(bounds);
      }, 300);
    }
  }, [ready, pins, selectedMac, hoveredMac]);

  // 이름 툴팁 — 호버 우선, 없으면 선택된 마커 위에.
  //
  // pins 가 의존성에 있어 폴링마다 이 effect 가 다시 돈다. 그때마다 setMap 을
  // 부르면 오버레이 DOM 이 떨어졌다 붙어서 마우스를 올린 동안 툴팁이 깜빡인다
  // (2026-09-02 현장 보고). 그래서 지금 떠 있는 내용을 기억해 두고, 실제로
  // 달라진 것만 손댄다.
  useEffect(() => {
    const maps = mapsRef.current;
    const map = mapRef.current;
    const tooltip = tooltipRef.current;
    const el = tooltipElRef.current;
    if (!ready || !maps || !map || !tooltip || !el) return;

    const target = hoveredMac ?? selectedMac;
    const entry = target ? entriesRef.current.get(target) : undefined;
    if (!entry) {
      if (shownTooltipRef.current) {
        tooltip.setMap(null);
        shownTooltipRef.current = null;
      }
      return;
    }

    const { pin } = entry;
    const text =
      (pin.label || pin.mac) + (pin.position_source !== 'device' ? ' · 위치 미입력' : '');
    const shown = shownTooltipRef.current;
    if (shown && shown.mac === pin.mac && shown.text === text) {
      // 같은 단말, 같은 문구 — 폴링이 새 배열을 줬을 뿐이다. 건드리지 않는다.
      if (shown.lat !== pin.lat || shown.lng !== pin.lng) {
        tooltip.setPosition(new maps.LatLng(pin.lat, pin.lng));
        shownTooltipRef.current = { ...shown, lat: pin.lat, lng: pin.lng };
      }
      return;
    }

    if (text !== shown?.text) el.textContent = text;
    tooltip.setPosition(new maps.LatLng(pin.lat, pin.lng));
    // 이미 떠 있으면 다시 붙이지 않는다 — 그게 깜빡임의 원인이다.
    if (!shown) {
      tooltip.setMap(map);
      // SDK 가 만든 바깥 래퍼에도 이벤트를 통과시킨다. 내용(el)에만 걸어두면
      // 래퍼가 커서를 가로채 마커에 mouseout 이 걸린다.
      if (el.parentElement) el.parentElement.style.pointerEvents = 'none';
    }
    shownTooltipRef.current = { mac: pin.mac, text, lat: pin.lat, lng: pin.lng };
  }, [ready, hoveredMac, selectedMac, pins]);

  // 목록(또는 마커)에서 선택하면 그 단말로 확대해서 이동한다.
  //
  // 이미 더 가까이 보고 있으면 확대 수준은 그대로 두고 위치만 옮긴다 — 사용자가
  // 잡아둔 축척을 멀어지는 쪽으로 바꾸지 않는다. 확대는 클러스터도 풀어준다
  // (SELECT_LEVEL 은 CLUSTER_MIN_LEVEL 보다 가까워야 한다).
  useEffect(() => {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!ready || !maps || !map || !selectedMac) return;
    const entry = entriesRef.current.get(selectedMac);
    if (!entry) return;
    const target = new maps.LatLng(entry.pin.lat, entry.pin.lng);
    if (map.getLevel() > SELECT_LEVEL) {
      // setLevel 의 anchor 는 "이 점을 화면 그 자리에 둔 채 확대"다. 그 뒤 panTo 로
      // 가운데로 가져온다 — 순서를 바꾸면 확대 중 목표점이 화면 밖으로 튄다.
      map.setLevel(SELECT_LEVEL, { anchor: target });
    }
    map.panTo(target);
  }, [ready, selectedMac]);

  if (sdkError) {
    return <div className="empty">지도를 불러오지 못했습니다 — {sdkError}</div>;
  }
  // 경계를 하나라도 그렸으면 출처를 표시한다. 「구역의 도형」은 공공누리 제1유형이고
  // 출처 표시가 그 유일한 의무다(지도 설계 §4.8).
  const showsBoundary = villages.some((v) => v.boundary);
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%', borderRadius: 12 }} />
      {showsBoundary && <div className="map-credit">마을 경계 : 행정안전부 주소정보제공</div>}
    </div>
  );
}
