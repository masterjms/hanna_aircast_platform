/**
 * 방송 대상 — 기관 트리에서 마을을 고른다.
 *
 * 기관 칸을 체크하면 그 아래(깊이 무관) 마을이 전부 선택된다 — 「경기도」를 체크하면
 * 경기도 아래 모든 마을. 기관 관리자는 자기 기관이 트리 뿌리라서 도 계정이면 그 도를
 * 바로 고를 수 있다. 이장은 기관이 없으므로 담당 마을만 평평하게 나온다.
 *
 * 선택의 정본은 마을 id 목록이다. 서버에는 마을 목록으로 보낸다 — 스케줄 실행기가
 * 「관할 전체」를 마을로 펼쳐 보내는 것과 같아서 방송 이력·겹침 검사·단말 프로토콜이
 * 그대로다. 온라인 단말이 없는 마을은 방송이 나가지 않으므로 고를 수 없고, 기관 체크에도
 * 들어가지 않는다.
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import type { Organization, Village } from '../../api/types';
import { buildForest, subtreeVillages, type OrgForest, type OrgNode } from '../../lib/orgtree';

const selectable = (v: Village) => v.online_count > 0;

function TriCheckbox({
  checked,
  indeterminate,
  disabled,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  disabled: boolean;
  onChange: (on: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

/**
 * 사람이 읽는 선택 요약. 아래 마을이 전부 선택된 기관은 「경기도 전체」 하나로 접고,
 * 나머지는 마을 이름으로 쓴다.
 */
export function summarizeSelection(forest: OrgForest, ids: number[]): string[] {
  const on = new Set(ids);
  const out: string[] = [];
  const walk = (node: OrgNode) => {
    const pick = subtreeVillages(node).filter(selectable);
    if (pick.length > 0 && pick.every((v) => on.has(v.id))) {
      out.push(`${node.org.name} 전체`);
      return;
    }
    node.children.forEach(walk);
    for (const v of node.villages) if (on.has(v.id)) out.push(v.name);
  };
  forest.roots.forEach(walk);
  for (const v of forest.orphans) if (on.has(v.id)) out.push(v.name);
  return out;
}

export function VillageTreePicker({
  orgs,
  villages,
  value,
  onChange,
  renderCount,
}: {
  orgs: Organization[];
  villages: Village[];
  value: number[];
  onChange: (ids: number[]) => void;
  renderCount: (online: number, total: number) => ReactNode;
}) {
  const forest = useMemo(() => buildForest(orgs, villages), [orgs, villages]);
  const [collapsed, setCollapsed] = useState<Set<number>>(() => new Set());
  const selected = new Set(value);

  const setMany = (list: Village[], on: boolean) => {
    const next = new Set(selected);
    for (const v of list) {
      if (on) next.add(v.id);
      else next.delete(v.id);
    }
    onChange([...next]);
  };

  const toggleCollapse = (id: number) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const villageRow = (v: Village) => {
    const on = selected.has(v.id);
    return (
      <li key={`v${v.id}`}>
        <div className="tree__row">
          <span className="tree__caret tree__caret--none" aria-hidden="true" />
          <label className={on ? 'is-on' : undefined}>
            <input
              type="checkbox"
              checked={on}
              disabled={!selectable(v)}
              onChange={(e) => setMany([v], e.target.checked)}
            />
            <span className="tree__name">{v.name}</span>
            <span className="tree__kind">{renderCount(v.online_count, v.device_count)}</span>
          </label>
        </div>
      </li>
    );
  };

  const orgBlock = (node: OrgNode): ReactNode => {
    const o = node.org;
    const all = subtreeVillages(node);
    const pick = all.filter(selectable);
    const onCount = pick.filter((v) => selected.has(v.id)).length;
    const checked = pick.length > 0 && onCount === pick.length;
    const hasKids = node.children.length > 0 || node.villages.length > 0;
    const isCollapsed = collapsed.has(o.id);
    const online = all.reduce((n, v) => n + v.online_count, 0);
    const total = all.reduce((n, v) => n + v.device_count, 0);
    return (
      <li key={`o${o.id}`}>
        <div className="tree__row">
          {hasKids ? (
            <button
              type="button"
              className="tree__caret"
              aria-label={isCollapsed ? `${o.name} 펼치기` : `${o.name} 접기`}
              aria-expanded={!isCollapsed}
              onClick={() => toggleCollapse(o.id)}
            >
              {isCollapsed ? '▸' : '▾'}
            </button>
          ) : (
            <span className="tree__caret tree__caret--none" aria-hidden="true" />
          )}
          <label className={checked ? 'is-on' : undefined}>
            <TriCheckbox
              checked={checked}
              indeterminate={onCount > 0 && !checked}
              disabled={pick.length === 0}
              onChange={(on) => setMany(pick, on)}
            />
            <span className="tree__name strong">{o.name}</span>
            <span className="tree__kind">
              마을 {all.length}곳 · {renderCount(online, total)}
            </span>
          </label>
        </div>
        {hasKids && !isCollapsed && (
          <ul>
            {node.children.map(orgBlock)}
            {node.villages.map(villageRow)}
          </ul>
        )}
      </li>
    );
  };

  if (orgs.length === 0) {
    // 이장 — 기관이 없고 담당 마을만 있다.
    return <ul className="tree tree-picker">{villages.map(villageRow)}</ul>;
  }

  return (
    <ul className="tree tree-picker">
      {forest.roots.map(orgBlock)}
      {forest.orphans.length > 0 && (
        <li>
          <div className="tree__group">기관 없음</div>
          <ul>{forest.orphans.map(villageRow)}</ul>
        </li>
      )}
    </ul>
  );
}
