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
 */

import { useEffect, useRef, useState } from 'react';

import { loadKakaoMaps } from '../lib/kakao';
import type { MapPin } from '../api/types';

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Entry {
  marker: any;
  pin: MapPin;
  imageKey: string;
}

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

export function MapView({
  jsKey,
  pins,
  selectedMac,
  hoveredMac,
  onSelect,
  onHover,
}: {
  jsKey: string;
  pins: MapPin[];
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
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const fittedRef = useRef(false);
  const [sdkError, setSdkError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // 콜백을 ref 로 — 마커 리스너가 최신 핸들러를 보게 하면서 재부착은 피한다.
  const onSelectRef = useRef(onSelect);
  const onHoverRef = useRef(onHover);
  onSelectRef.current = onSelect;
  onHoverRef.current = onHover;

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
        // 이름 툴팁 — 호버/선택된 마커 위에 하나만 띄워 재사용한다.
        const el = document.createElement('div');
        el.className = 'map-tooltip';
        tooltipElRef.current = el;
        tooltipRef.current = new maps.CustomOverlay({
          content: el,
          yAnchor: 1.15, // 핀 머리 위
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

  // 마커 동기화 — mac 별 재사용, 사라진 것만 제거.
  useEffect(() => {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!ready || !maps || !map) return;

    const seen = new Set<string>();
    for (const pin of pins) {
      seen.add(pin.mac);
      const selected = pin.mac === selectedMac;
      const hovered = pin.mac === hoveredMac;
      let entry = entriesRef.current.get(pin.mac);
      if (!entry) {
        const { img, key } = markerImage(pin, selected);
        const marker = new maps.Marker({
          position: new maps.LatLng(pin.lat, pin.lng),
          image: img,
          map,
        });
        maps.event.addListener(marker, 'click', () => onSelectRef.current(pin.mac));
        maps.event.addListener(marker, 'mouseover', () => onHoverRef.current(pin.mac));
        maps.event.addListener(marker, 'mouseout', () => onHoverRef.current(null));
        entry = { marker, pin, imageKey: key };
        entriesRef.current.set(pin.mac, entry);
      } else {
        if (entry.pin.lat !== pin.lat || entry.pin.lng !== pin.lng) {
          entry.marker.setPosition(new maps.LatLng(pin.lat, pin.lng));
        }
        const { img, key } = markerImage(pin, selected);
        // setImage 는 이미지가 실제로 바뀔 때만 — 폴링마다 부르면 깜빡인다.
        if (key !== entry.imageKey) {
          entry.marker.setImage(img);
          entry.imageKey = key;
        }
      }
      entry.pin = pin;
      entry.marker.setZIndex(selected ? 30 : hovered ? 20 : 1);
    }
    for (const [mac, entry] of entriesRef.current) {
      if (!seen.has(mac)) {
        entry.marker.setMap(null);
        entriesRef.current.delete(mac);
      }
    }

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
    if (!shown) tooltip.setMap(map);
    shownTooltipRef.current = { mac: pin.mac, text, lat: pin.lat, lng: pin.lng };
  }, [ready, hoveredMac, selectedMac, pins]);

  // 목록에서 선택하면 그 마커로 이동.
  useEffect(() => {
    const maps = mapsRef.current;
    const map = mapRef.current;
    if (!ready || !maps || !map || !selectedMac) return;
    const entry = entriesRef.current.get(selectedMac);
    if (entry) map.panTo(new maps.LatLng(entry.pin.lat, entry.pin.lng));
  }, [ready, selectedMac]);

  if (sdkError) {
    return <div className="empty">지도를 불러오지 못했습니다 — {sdkError}</div>;
  }
  return <div ref={containerRef} style={{ width: '100%', height: '100%', borderRadius: 12 }} />;
}
