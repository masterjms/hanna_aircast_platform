/**
 * 위치 미세 보정 미니맵 — 주소 검색 좌표를 사람이 지도에서 끌어 맞춘다.
 *
 * 왜 필요한가: 지오코딩은 "그 지번의 대표 지점" 좌표를 준다. 아파트 단지는
 * 한 지번에 동이 여러 개라 어느 동을 적어도 대표점 하나로 떨어진다(109동을
 * 검색해도 101동 근처 — 2026-08-31 실측, 오차 ~100m). 카카오·네이버 모두
 * 동 단위 지오코딩은 없어서, 정확한 마커는 사람이 지도에서 한 번 찍는 것이
 * 업계 표준이다(배달 앱의 "지도에서 위치 확인" 단계가 같은 이유).
 *
 * 마커를 드래그하거나 지도를 클릭하면 onChange 로 좌표가 올라간다.
 */

import { useEffect, useRef, useState } from 'react';

import { loadKakaoMaps } from '../lib/kakao';

/* eslint-disable @typescript-eslint/no-explicit-any */

export function LocationPickerMap({
  jsKey,
  lat,
  lng,
  onChange,
}: {
  jsKey: string;
  lat: number;
  lng: number;
  onChange: (lat: number, lng: number) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const markerRef = useRef<any>(null);
  const mapsRef = useRef<any>(null);
  //: 사용자가 끌어서 생긴 변경인지 구분 — onChange 로 올라간 좌표가 props 로
  //: 되돌아올 때 지도를 다시 움직이면 드래그가 툭툭 끊긴다.
  const internalRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    let alive = true;
    loadKakaoMaps(jsKey)
      .then((maps) => {
        if (!alive || !containerRef.current) return;
        mapsRef.current = maps;
        const center = new maps.LatLng(lat, lng);
        const map = new maps.Map(containerRef.current, { center, level: 3 });
        const marker = new maps.Marker({ position: center, map, draggable: true });
        mapRef.current = map;
        markerRef.current = marker;

        const report = (pos: any) => {
          internalRef.current = true;
          onChangeRef.current(pos.getLat(), pos.getLng());
        };
        maps.event.addListener(marker, 'dragend', () => report(marker.getPosition()));
        maps.event.addListener(map, 'click', (e: any) => {
          marker.setPosition(e.latLng);
          report(e.latLng);
        });
        setTimeout(() => {
          map.relayout();
          map.setCenter(center);
        }, 300);
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jsKey]);

  // 새 주소를 검색해서 좌표가 밖에서 바뀌면 지도도 따라간다.
  useEffect(() => {
    const maps = mapsRef.current;
    if (!maps || !mapRef.current || !markerRef.current) return;
    if (internalRef.current) {
      internalRef.current = false;
      return;
    }
    const pos = new maps.LatLng(lat, lng);
    markerRef.current.setPosition(pos);
    mapRef.current.setCenter(pos);
  }, [lat, lng]);

  if (error) return <p className="hint hint--warn">지도를 불러오지 못했습니다 — {error}</p>;
  return (
    <>
      <div ref={containerRef} style={{ width: '100%', height: 220, borderRadius: 8 }} />
      <p className="hint">
        주소 검색은 지번 대표 지점까지만 정확합니다(아파트는 단지당 한 점). 마커를 끌거나
        지도를 클릭해 실제 설치 위치(동 앞 등)로 맞춰 주세요 — 저장하면 그 좌표가 쓰입니다.
      </p>
    </>
  );
}
