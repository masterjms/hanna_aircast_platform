/**
 * 카카오맵 JS SDK 로더.
 *
 * JS 키는 서버(.env)에서 /api/dashboard/map 응답으로 받아 동적으로 로드한다 —
 * 빌드 산출물에 키를 굽지 않아야 온프레미스 전환 때 재빌드가 필요 없다.
 * autoload=false + kakao.maps.load 콜백이 공식 동적 로드 방식이다.
 */

/* SDK 에 타입 정의가 없어 최소한으로 다룬다 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type KakaoMaps = any;

let loaded: Promise<KakaoMaps> | null = null;

export function loadKakaoMaps(jsKey: string): Promise<KakaoMaps> {
  if (loaded) return loaded;
  loaded = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    // libraries=clusterer: 마커 클러스터러(지도 설계 §4.4). 한 마을에 단말이 몰리면
    // 핀이 겹쳐 안 보이는 것을 화면에서 묶어 푼다 — 데이터는 다 받고 렌더링만 묶는다.
    script.src =
      `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(jsKey)}` +
      '&autoload=false&libraries=clusterer';
    script.async = true;
    script.onload = () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const kakao = (window as any).kakao;
      if (!kakao?.maps?.load) {
        reject(new Error('카카오맵 SDK 초기화 실패 — JS 키와 도메인 등록을 확인하세요.'));
        return;
      }
      kakao.maps.load(() => resolve(kakao.maps));
    };
    script.onerror = () => {
      loaded = null; // 다음 시도에서 다시 로드할 수 있게
      reject(new Error('카카오맵 SDK 를 불러오지 못했습니다 (네트워크 또는 도메인 미등록).'));
    };
    document.head.appendChild(script);
  });
  return loaded;
}
