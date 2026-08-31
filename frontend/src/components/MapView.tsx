/**
 * 카카오맵 래퍼 — 마커 그리기만 한다.
 *
 * 지도 설계 §4.6 의 경계선: 이 컴포넌트는 API·폴링·권한을 모른다. 받은 배열을
 * 그릴 뿐이다. 목록과의 연동도 부모의 selectedMac/hoveredMac 두 상태로만 만난다.
 *
 * 마커는 기본 핀 대신 CustomOverlay(div)를 쓴다 — 상태색(온라인/오프라인/무음)과
 * 선택 강조를 CSS 로 다루기 위해서다. 폴링마다 전부 재생성하지 않고 mac 별로
 * 재사용한다(§4.4 — 브라우저 부담의 관건).
 */

import { useEffect, useRef, useState } from 'react';

import { loadKakaoMaps } from '../lib/kakao';
import type { MapPin } from '../api/types';

/* eslint-disable @typescript-eslint/no-explicit-any */

interface Entry {
  overlay: any;
  el: HTMLDivElement;
  pin: MapPin;
}

function pinClass(pin: MapPin, selected: boolean, hovered: boolean): string {
  const status = !pin.online ? 'offline' : pin.live === 'RECONNECTING' ? 'warn' : 'ok';
  return [
    'map-pin',
    `map-pin--${status}`,
    pin.position_source !== 'device' ? 'map-pin--approx' : '',
    selected ? 'map-pin--selected' : '',
    hovered ? 'map-pin--hovered' : '',
  ]
    .filter(Boolean)
    .join(' ');
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
  const fittedRef = useRef(false);
  const [sdkError, setSdkError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // 콜백을 ref 로 — 오버레이 DOM 리스너가 최신 핸들러를 보게 하면서 재부착은 피한다.
  const onSelectRef = useRef(onSelect);
  const onHoverRef = useRef(onHover);
  onSelectRef.current = onSelect;
  onHoverRef.current = onHover;

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
        setReady(true);
      })
      .catch((e) => alive && setSdkError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
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
      let entry = entriesRef.current.get(pin.mac);
      if (!entry) {
        const el = document.createElement('div');
        el.addEventListener('click', (e) => {
          e.stopPropagation();
          onSelectRef.current(pin.mac);
        });
        el.addEventListener('mouseenter', () => onHoverRef.current(pin.mac));
        el.addEventListener('mouseleave', () => onHoverRef.current(null));
        const overlay = new maps.CustomOverlay({
          position: new maps.LatLng(pin.lat, pin.lng),
          content: el,
          yAnchor: 0.5,
          clickable: true,
        });
        overlay.setMap(map);
        entry = { overlay, el, pin };
        entriesRef.current.set(pin.mac, entry);
      } else if (entry.pin.lat !== pin.lat || entry.pin.lng !== pin.lng) {
        entry.overlay.setPosition(new maps.LatLng(pin.lat, pin.lng));
      }
      entry.pin = pin;
      entry.el.className = pinClass(pin, pin.mac === selectedMac, pin.mac === hoveredMac);
      entry.el.title = `${pin.label ?? pin.mac} · ${pin.village_name ?? '미배정'}${
        pin.position_source !== 'device' ? ' (위치 미입력 — 마을 좌표)' : ''
      }`;
      entry.el.textContent = pin.label ?? pin.mac.slice(-4);
    }
    for (const [mac, entry] of entriesRef.current) {
      if (!seen.has(mac)) {
        entry.overlay.setMap(null);
        entriesRef.current.delete(mac);
      }
    }

    // 첫 데이터에서 한 번만 전체 핀이 보이게 맞춘다 — 폴링마다 하면 지도가 널뛴다.
    if (!fittedRef.current && pins.length > 0) {
      fittedRef.current = true;
      const bounds = new maps.LatLngBounds();
      for (const pin of pins) bounds.extend(new maps.LatLng(pin.lat, pin.lng));
      map.setBounds(bounds);
    }
  }, [ready, pins, selectedMac, hoveredMac]);

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
