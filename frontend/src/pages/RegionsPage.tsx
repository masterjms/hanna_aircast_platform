/**
 * 지역 관리 (기관 관리자 이상) — 관리자 계층 설계 v2 (2026-09-12) §9.
 *
 * 기관 관리·마을 관리 두 화면을 **탐색기 하나**로 합쳤다. VS Code 의 파일 탐색기와
 * 같은 손놀림이다 — 사람들이 가장 많이 써 본 트리 조작이라서:
 *
 *   · 마디를 고르고 [새 기관] / [새 마을] → 그 자리에 입력칸이 열리고 Enter 로 만든다
 *   · F2 이름 바꾸기 · Delete 삭제 · ↑↓←→ 이동 · 우클릭 메뉴
 *   · 드래그로 옮기기 — 단, 구조가 바뀌는 일이라 놓을 때 한 번 묻는다(설계 §6)
 *
 * 기관(폴더)은 이름과 자리뿐이다. 주소·좌표는 마을(파일)에만 있다 — 지도에 찍히는 것은
 * 마을이라서다. 깊이 제한은 없다.
 *
 * 권한은 전부 백엔드가 판정한다. 여기서 버튼을 숨기는 것은 "눌러 봐야 안 되는 것"을 미리
 * 치우는 편의다: 기관 관리자는 자기 마디(트리의 뿌리)를 옮기거나 지울 수 없고, 뿌리
 * 기관은 최고 관리자만 만든다.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from 'react';

import { ApiError, api } from '../api/client';
import type { Organization, Village } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { Tile, VillageIcon, VillagePanel } from '../components/regions/VillagePanel';
import {
  buildForest,
  isUnder,
  pathLabel,
  pathOf,
  subtreeStats,
  type OrgForest,
  type OrgNode,
} from '../lib/orgtree';

// ── 트리 행 모델 ──────────────────────────────────────────────────────────
//: 행 키. 'o12' 기관 · 'v7' 마을 · 'orphans' 기관 없는 마을 묶음 · 'new' 입력 중인 행
type RowKey = string;
type Selection = { key: RowKey } | null;

interface Row {
  key: RowKey;
  kind: 'org' | 'village' | 'orphans' | 'new';
  depth: number;
  node?: OrgNode;
  village?: Village;
  /** 마을 행이 속한 기관. 기관 없는 마을은 null. */
  orgId: number | null;
  hasChildren: boolean;
  expanded: boolean;
  parentKey: RowKey | null;
  newKind?: 'org' | 'village';
}

interface Editing {
  mode: 'create' | 'rename';
  kind: 'org' | 'village';
  /** create: 붙일 기관(null = 뿌리 또는 기관 없음). rename: 대상 행. */
  parentId?: number | null;
  key?: RowKey;
  /** 입력칸을 어느 행 바로 아래에 그릴지. */
  afterKey: RowKey | null;
}

interface Menu {
  x: number;
  y: number;
  key: RowKey;
}

/** 트리의 실체 — 행과 달리 접힘·검색과 무관하게 항상 찾을 수 있다. */
type Entity =
  | { kind: 'org'; node: OrgNode; orgId: number; parentKey: RowKey | null }
  | { kind: 'village'; village: Village; orgId: number | null; parentKey: RowKey }
  | { kind: 'orphans'; orgId: null; parentKey: null };

/** 놓을 자리. org = 기관 행, orphans = 「기관 없음」 행(마을만), root = 트리의 빈 자리 */
type DropTarget = { kind: 'org'; orgId: number } | { kind: 'orphans' } | { kind: 'root' };

const ORPHANS_KEY = 'orphans';
const keyOfOrg = (id: number) => `o${id}`;
const keyOfVillage = (id: number) => `v${id}`;

function matches(q: string, name: string) {
  return name.toLowerCase().includes(q);
}

/** 검색어에 걸리는 마디(자기 또는 후손·마을 중 하나라도)만 남긴다. */
function nodeMatches(node: OrgNode, q: string): boolean {
  if (matches(q, node.org.name)) return true;
  if (node.villages.some((v) => matches(q, v.name))) return true;
  return node.children.some((c) => nodeMatches(c, q));
}

function flatten(
  forest: OrgForest,
  expanded: Set<RowKey>,
  query: string,
  editing: Editing | null,
  showOrphans: boolean,
): Row[] {
  const q = query.trim().toLowerCase();
  const rows: Row[] = [];
  const isOpen = (key: RowKey) => (q ? true : expanded.has(key));

  const newRow = (afterKey: RowKey | null, depth: number, parentKey: RowKey | null) => {
    if (editing?.mode === 'create' && editing.afterKey === afterKey) {
      rows.push({
        key: 'new',
        kind: 'new',
        depth,
        orgId: editing.parentId ?? null,
        hasChildren: false,
        expanded: false,
        parentKey,
        newKind: editing.kind,
      });
    }
  };

  const walk = (node: OrgNode, parentKey: RowKey | null) => {
    if (q && !nodeMatches(node, q)) return;
    const key = keyOfOrg(node.org.id);
    const hasChildren = node.children.length > 0 || node.villages.length > 0;
    const open = isOpen(key);
    rows.push({
      key,
      kind: 'org',
      depth: node.depth,
      node,
      orgId: node.org.id,
      hasChildren,
      expanded: open,
      parentKey,
    });
    // 입력칸은 "폴더 바로 아래 첫 줄"에 — VS Code 와 같다. 폴더가 접혀 있어도
    // 입력 중에는 편집기가 강제로 연다(아래 useEffect).
    newRow(key, node.depth + 1, key);
    if (!open && !(editing?.mode === 'create' && editing.afterKey === key)) return;
    for (const c of node.children) walk(c, key);
    for (const v of node.villages) {
      if (q && !matches(q, v.name) && !matches(q, node.org.name)) continue;
      rows.push({
        key: keyOfVillage(v.id),
        kind: 'village',
        depth: node.depth + 1,
        village: v,
        orgId: node.org.id,
        hasChildren: false,
        expanded: false,
        parentKey: key,
      });
    }
  };

  // 뿌리에 새 기관을 만들 때는 맨 위에 입력칸.
  newRow(null, 0, null);
  for (const r of forest.roots) walk(r, null);

  // 검색 중에는 걸리는 마을이 있을 때만 「기관 없음」 묶음을 보인다.
  const orphansVisible =
    showOrphans && (!q || forest.orphans.some((v) => matches(q, v.name)) || editing?.afterKey === ORPHANS_KEY);
  if (orphansVisible) {
    const open = isOpen(ORPHANS_KEY);
    rows.push({
      key: ORPHANS_KEY,
      kind: 'orphans',
      depth: 0,
      orgId: null,
      hasChildren: forest.orphans.length > 0,
      expanded: open,
      parentKey: null,
    });
    newRow(ORPHANS_KEY, 1, ORPHANS_KEY);
    if (open || (editing?.mode === 'create' && editing.afterKey === ORPHANS_KEY)) {
      for (const v of forest.orphans) {
        if (q && !matches(q, v.name)) continue;
        rows.push({
          key: keyOfVillage(v.id),
          kind: 'village',
          depth: 1,
          village: v,
          orgId: null,
          hasChildren: false,
          expanded: false,
          parentKey: ORPHANS_KEY,
        });
      }
    }
  }
  return rows;
}

// ── 아이콘 ────────────────────────────────────────────────────────────────
function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg className="trow__svg" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      {open ? (
        <path
          d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3.2l1.3 1.5h5.5a1 1 0 0 1 1 1V6H3.4a1 1 0 0 0-.95.68L1.5 9.5v-6zm.4 9.5 1.4-5.2a.5.5 0 0 1 .48-.36h11.1l-1.5 5.2a.5.5 0 0 1-.48.36H1.9z"
          fill="currentColor"
        />
      ) : (
        <path
          d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3.2l1.3 1.5h5.5a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-9z"
          fill="currentColor"
        />
      )}
    </svg>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={`trow__chev${open ? ' is-open' : ''}`}
      viewBox="0 0 16 16"
      width="12"
      height="12"
      aria-hidden="true"
    >
      <path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );
}

function ToolButton({
  title,
  onClick,
  disabled,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className="iconbtn"
      title={title}
      aria-label={title}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

// ── 화면 ─────────────────────────────────────────────────────────────────
export function RegionsPage() {
  const { user: me, isSuperAdmin } = useAuth();
  //: 기관 관리자의 자기 마디. 트리의 뿌리이고, 옮기거나 지울 수 없다.
  const myRootId = !isSuperAdmin ? (me?.organization_id ?? null) : null;

  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [villages, setVillages] = useState<Village[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Selection>(null);
  const [expanded, setExpanded] = useState<Set<RowKey>>(new Set());
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<Editing | null>(null);
  const [draft, setDraft] = useState('');
  const [menu, setMenu] = useState<Menu | null>(null);
  const [dragKey, setDragKey] = useState<RowKey | null>(null);
  const [dropKey, setDropKey] = useState<RowKey | null>(null);
  const [busy, setBusy] = useState(false);

  const treeRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const fail = (err: unknown, fallback: string) =>
    setError(err instanceof ApiError ? err.message : fallback);

  const load = useCallback(async () => {
    try {
      const [o, v] = await Promise.all([api.organizations.list(), api.villages.list()]);
      setOrgs(o);
      setVillages(v);
      setError(null);
    } catch (err) {
      fail(err, '목록을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const forest = useMemo(() => buildForest(orgs, villages), [orgs, villages]);
  //: 기관 없는 마을 묶음은 최고 관리자에게만 있다(백엔드가 그렇게 걸러 준다).
  const showOrphans = isSuperAdmin && (forest.orphans.length > 0 || orgs.length === 0);

  // 처음 불러오면 뿌리를 펼쳐 둔다 — 빈 폴더 아이콘만 보이면 뭘 해야 할지 모른다.
  const initialised = useRef(false);
  useEffect(() => {
    if (initialised.current || loading) return;
    initialised.current = true;
    setExpanded(new Set([...forest.roots.map((r) => keyOfOrg(r.org.id)), ORPHANS_KEY]));
  }, [loading, forest]);

  const rows = useMemo(
    () => flatten(forest, expanded, query, editing, showOrphans),
    [forest, expanded, query, editing, showOrphans],
  );
  const rowByKey = useMemo(() => new Map(rows.map((r) => [r.key, r])), [rows]);
  //: 화면에 보이는 행. 부모가 접혀 있으면 없다 — 키보드 이동에만 쓴다.
  const selectedRow = selected ? (rowByKey.get(selected.key) ?? null) : null;

  // 실체는 트리에서 찾는다 — 부모를 접어도, 검색으로 걸러져도 선택은 살아 있어야 한다.
  // 예전에는 보이는 행에서만 찾아서 폴더를 접으면 오른쪽 패널이 비어 버렸다.
  const villageById = useMemo(() => new Map(villages.map((v) => [v.id, v])), [villages]);
  const entityOf = useCallback(
    (key: RowKey): Entity | null => {
      if (key === ORPHANS_KEY) return { kind: 'orphans', orgId: null, parentKey: null };
      if (key.startsWith('o')) {
        const node = forest.byId.get(Number(key.slice(1)));
        return node
          ? {
              kind: 'org',
              node,
              orgId: node.org.id,
              parentKey: node.parent ? keyOfOrg(node.parent.org.id) : null,
            }
          : null;
      }
      if (key.startsWith('v')) {
        const village = villageById.get(Number(key.slice(1)));
        if (!village) return null;
        const orgId =
          village.organization_id !== null && forest.byId.has(village.organization_id)
            ? village.organization_id
            : null;
        return { kind: 'village', village, orgId, parentKey: orgId === null ? ORPHANS_KEY : keyOfOrg(orgId) };
      }
      return null;
    },
    [forest, villageById],
  );
  const selectedEntity = selected ? entityOf(selected.key) : null;

  // 선택된 것이 사라지면(삭제) 선택을 푼다.
  useEffect(() => {
    if (selected && !loading && !entityOf(selected.key)) setSelected(null);
  }, [selected, loading, entityOf]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  // 우클릭 메뉴는 아무 데나 누르거나 Esc 로 닫는다.
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (e: globalThis.KeyboardEvent) => e.key === 'Escape' && close();
    window.addEventListener('mousedown', close);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', close);
      window.removeEventListener('keydown', onKey);
    };
  }, [menu]);

  const toggle = (key: RowKey, open?: boolean) =>
    setExpanded((cur) => {
      const next = new Set(cur);
      const want = open ?? !next.has(key);
      if (want) next.add(key);
      else next.delete(key);
      return next;
    });

  const expandAll = () =>
    setExpanded(new Set([...forest.byId.keys()].map(keyOfOrg).concat(ORPHANS_KEY)));
  const collapseAll = () => setExpanded(new Set());

  // ── 권한(편의) ──────────────────────────────────────────────────────────
  const canDeleteOrg = (node: OrgNode) =>
    node.org.child_count === 0 &&
    node.org.village_count === 0 &&
    node.org.user_count === 0 &&
    node.org.id !== myRootId;
  const deleteBlockReason = (node: OrgNode) => {
    if (node.org.id === myRootId) return '자기 기관은 지울 수 없습니다';
    const parts = [
      node.org.child_count > 0 && `하위 기관 ${node.org.child_count}`,
      node.org.village_count > 0 && `마을 ${node.org.village_count}`,
      node.org.user_count > 0 && `계정 ${node.org.user_count}`,
    ].filter(Boolean);
    return parts.length ? `${parts.join(' · ')} 이(가) 있어 지울 수 없습니다. 먼저 옮기거나 지우세요.` : '';
  };
  const canDragOrg = (node: OrgNode) => node.org.id !== myRootId;

  // ── 만들기 · 이름 바꾸기 ────────────────────────────────────────────────
  /**
   * 새 기관/마을을 어디에 붙일지. 고른 것이 기관이면 그 안에, 마을이면 그 마을의 기관 안에
   * (VS Code 에서 파일을 고른 채 "새 파일"을 누르면 옆에 생기는 것과 같다). 아무것도 안
   * 골랐으면 최고 관리자는 뿌리, 기관 관리자는 자기 기관.
   */
  const targetParent = (): { parentId: number | null; afterKey: RowKey | null } => {
    const ent = selectedEntity;
    if (ent?.kind === 'org') return { parentId: ent.orgId, afterKey: keyOfOrg(ent.orgId) };
    if (ent?.kind === 'village') {
      return ent.orgId === null
        ? { parentId: null, afterKey: ORPHANS_KEY }
        : { parentId: ent.orgId, afterKey: keyOfOrg(ent.orgId) };
    }
    if (ent?.kind === 'orphans') return { parentId: null, afterKey: ORPHANS_KEY };
    if (myRootId !== null && forest.byId.has(myRootId))
      return { parentId: myRootId, afterKey: keyOfOrg(myRootId) };
    return { parentId: null, afterKey: null };
  };

  const startCreate = (kind: 'org' | 'village') => {
    const { parentId, afterKey } = targetParent();
    // 기관 관리자는 뿌리에 기관을 못 만든다. 마을은 기관 없이 못 만든다(최고 관리자만).
    if (!isSuperAdmin && parentId === null) return;
    const finalAfter = kind === 'village' && parentId === null ? ORPHANS_KEY : afterKey;
    if (finalAfter) toggle(finalAfter, true);
    setQuery('');
    setDraft('');
    setMenu(null);
    setEditing({ mode: 'create', kind, parentId, afterKey: finalAfter });
  };

  const startRename = (key: RowKey) => {
    const ent = entityOf(key);
    if (!ent || ent.kind === 'orphans') return;
    // 접혀 있으면 펼친다 — 입력칸은 보이는 행에만 그려진다.
    if (ent.parentKey) toggle(ent.parentKey, true);
    setDraft(ent.kind === 'org' ? ent.node.org.name : ent.village.name);
    setMenu(null);
    setEditing({ mode: 'rename', kind: ent.kind, key, afterKey: null });
  };

  const cancelEdit = () => {
    setEditing(null);
    setDraft('');
    treeRef.current?.focus();
  };

  //: 커밋 진행 중 표시. Enter 로 커밋하는 동안 입력칸이 disabled 되며 blur 가 한 번 더 올 수
  //: 있는데, 그때 같은 커밋이 두 번 나가면 안 된다. 상태(busy)는 렌더 한 박자 늦어 ref 로 잡는다.
  const committingRef = useRef(false);

  const commitEdit = async () => {
    if (!editing || committingRef.current) return;
    const token = editing;
    const name = draft.trim();
    if (!name) {
      cancelEdit();
      return;
    }
    committingRef.current = true;
    setBusy(true);
    setError(null);
    try {
      if (editing.mode === 'create') {
        if (editing.kind === 'org') {
          const created = await api.organizations.create({ name, parent_id: editing.parentId ?? null });
          await load();
          setSelected({ key: keyOfOrg(created.id) });
        } else {
          const created = await api.villages.create({ name, organization_id: editing.parentId ?? null });
          await load();
          setSelected({ key: keyOfVillage(created.id) });
        }
      } else if (editing.key) {
        const ent = entityOf(editing.key);
        if (ent?.kind === 'org' && ent.node.org.name !== name)
          await api.organizations.update(ent.node.org.id, { name });
        if (ent?.kind === 'village' && ent.village.name !== name)
          await api.villages.update(ent.village.id, { name });
        await load();
      }
      // 그사이 다른 입력칸이 열렸으면(blur 로 커밋되는 동안 [새 마을]을 눌렀을 때) 그건 둔다.
      setEditing((cur) => (cur === token ? null : cur));
      setDraft((cur) => (editing === token ? '' : cur));
      treeRef.current?.focus();
    } catch (err) {
      fail(err, '저장에 실패했습니다.');
      // 입력칸은 그대로 둔다 — 이름을 고쳐 다시 Enter 칠 수 있게.
      inputRef.current?.focus();
    } finally {
      committingRef.current = false;
      setBusy(false);
    }
  };

  // ── 삭제 ────────────────────────────────────────────────────────────────
  const remove = async (key: RowKey) => {
    const ent = entityOf(key);
    setMenu(null);
    if (!ent || ent.kind === 'orphans') return;
    setError(null);
    try {
      if (ent.kind === 'org') {
        if (!canDeleteOrg(ent.node)) {
          setError(deleteBlockReason(ent.node));
          return;
        }
        if (!window.confirm(`기관 "${ent.node.org.name}" 을(를) 삭제할까요?`)) return;
        await api.organizations.remove(ent.node.org.id);
        setSelected(ent.parentKey ? { key: ent.parentKey } : null);
      } else {
        const v = ent.village;
        const warning =
          v.device_count > 0
            ? `${v.name} 을(를) 삭제하면 소속 단말 ${v.device_count}대가 미배정으로 돌아갑니다. 계속할까요?`
            : `${v.name} 을(를) 삭제할까요?`;
        if (!window.confirm(warning)) return;
        await api.villages.remove(v.id);
        setSelected(ent.parentKey !== ORPHANS_KEY ? { key: ent.parentKey } : null);
      }
      await load();
    } catch (err) {
      fail(err, '삭제에 실패했습니다.');
    }
  };

  // ── 옮기기(드래그) ──────────────────────────────────────────────────────
  //: 끌고 있는 행. ref 인 이유: dragstart 안에서 setState 로 화면을 바꾸면 Chrome 이 "드래그
  //: 대상이 바뀌었다"고 보고 드래그를 즉시 취소한다(2026-09-12 운영에서 실제로 안 끌렸다).
  //: 판정은 ref 로, 표시(흐리게·놓기 영역)는 한 박자 뒤(setTimeout)에 state 로 한다.
  const dragRef = useRef<RowKey | null>(null);

  /** 이 실체를 target 에 놓아도 되나. */
  const canDropOn = (srcKey: RowKey, target: DropTarget): boolean => {
    const src = entityOf(srcKey);
    if (!src || src.kind === 'orphans') return false;
    if (src.kind === 'org') {
      if (!canDragOrg(src.node)) return false;
      if (target.kind === 'orphans') return false; // 「기관 없음」은 마을만 받는다
      if (target.kind === 'root') return isSuperAdmin && src.node.org.parent_id !== null;
      const node = forest.byId.get(target.orgId);
      if (!node || node === src.node || isUnder(node, src.node)) return false; // 순환
      return src.node.org.parent_id !== target.orgId;
    }
    // 마을
    if (target.kind === 'org') return src.orgId !== target.orgId;
    return isSuperAdmin && src.orgId !== null; // 기관 없음으로 — 최고 관리자만
  };

  const move = async (srcKey: RowKey, target: DropTarget) => {
    const src = entityOf(srcKey);
    if (!src || src.kind === 'orphans' || !canDropOn(srcKey, target)) return;
    const targetOrgId = target.kind === 'org' ? target.orgId : null;
    const targetName =
      target.kind === 'org'
        ? (forest.byId.get(target.orgId)?.org.name ?? '기관')
        : src.kind === 'org'
          ? '최상위'
          : '기관 없음(최고 관리자만 봄)';
    const srcName = src.kind === 'org' ? src.node.org.name : src.village.name;
    // 옮기면 누가 보고 방송하는지가 바뀐다. 손가락이 미끄러져 옆 기관에 떨어지는 일에
    // 확인 없이 일어나면 안 된다(설계 §6.2).
    const what =
      src.kind === 'org'
        ? '이 기관과 그 아래 전부의 관할이 바뀝니다.'
        : '이 마을을 보는 계정이 바뀝니다. 단말·주소·이력은 그대로입니다.';
    if (!window.confirm(`"${srcName}" 을(를) "${targetName}" 아래로 옮길까요?\n${what}`)) return;
    setError(null);
    try {
      if (src.kind === 'org') await api.organizations.update(src.node.org.id, { parent_id: targetOrgId });
      else await api.villages.update(src.village.id, { organization_id: targetOrgId });
      if (targetOrgId !== null) toggle(keyOfOrg(targetOrgId), true);
      else if (src.kind === 'village') toggle(ORPHANS_KEY, true);
      await load();
    } catch (err) {
      fail(err, '옮기지 못했습니다.');
    }
  };

  const onDragStart = (e: DragEvent, row: Row) => {
    if (row.kind === 'org' && row.node && !canDragOrg(row.node)) {
      e.preventDefault();
      return;
    }
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', row.key);
    dragRef.current = row.key;
    const key = row.key;
    window.setTimeout(() => setDragKey(key), 0);
  };
  const onDragOver = (e: DragEvent, target: DropTarget, targetKey: RowKey) => {
    const src = dragRef.current;
    if (src && canDropOn(src, target)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      if (dropKey !== targetKey) setDropKey(targetKey);
    }
  };
  const onDrop = (e: DragEvent, target: DropTarget) => {
    e.preventDefault();
    const src = dragRef.current ?? e.dataTransfer.getData('text/plain');
    dragRef.current = null;
    setDragKey(null);
    setDropKey(null);
    if (src) void move(src, target);
  };
  const onDragEnd = () => {
    dragRef.current = null;
    setDragKey(null);
    setDropKey(null);
  };

  // ── 키보드 ──────────────────────────────────────────────────────────────
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (editing) return; // 입력칸이 자기 키를 처리한다
    const idx = selectedRow ? rows.indexOf(selectedRow) : -1;
    const pick = (i: number) => {
      const r = rows[Math.max(0, Math.min(rows.length - 1, i))];
      if (r) setSelected({ key: r.key });
    };
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        pick(idx + 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        pick(idx - 1);
        break;
      case 'ArrowRight':
        e.preventDefault();
        if (selectedRow?.hasChildren && !selectedRow.expanded) toggle(selectedRow.key, true);
        else pick(idx + 1);
        break;
      case 'ArrowLeft':
        e.preventDefault();
        if (selectedRow?.hasChildren && selectedRow.expanded) toggle(selectedRow.key, false);
        else if (selectedRow?.parentKey) setSelected({ key: selectedRow.parentKey });
        break;
      case 'Enter':
        if (selectedRow?.hasChildren) toggle(selectedRow.key);
        break;
      case 'F2':
        e.preventDefault();
        if (selectedRow) startRename(selectedRow.key);
        break;
      case 'Delete':
        e.preventDefault();
        if (selectedRow) void remove(selectedRow.key);
        break;
      case 'Escape':
        setSelected(null);
        break;
      default:
        return;
    }
  };

  const onContextMenu = (e: MouseEvent, row: Row) => {
    if (row.kind === 'new') return;
    e.preventDefault();
    setSelected({ key: row.key });
    setMenu({ x: e.clientX, y: e.clientY, key: row.key });
  };

  // ── 렌더 ────────────────────────────────────────────────────────────────
  const createTarget = targetParent();
  const canNewOrg = isSuperAdmin || createTarget.parentId !== null;
  const canNewVillage = isSuperAdmin || createTarget.parentId !== null;
  const targetNameForTitle =
    createTarget.parentId === null
      ? isSuperAdmin
        ? '최상위'
        : ''
      : (forest.byId.get(createTarget.parentId)?.org.name ?? '');

  const renderEditInput = (row: Row) => (
    <input
      ref={inputRef}
      className="trow__input"
      type="text"
      value={draft}
      placeholder={row.kind === 'new' && row.newKind === 'org' ? '기관 이름' : '마을 이름'}
      onChange={(e) => setDraft(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          void commitEdit();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          cancelEdit();
        }
        e.stopPropagation();
      }}
      onBlur={() => {
        // VS Code 와 같다: 비우고 나가면 취소, 적고 나가면 만든다.
        if (!committingRef.current) void commitEdit();
      }}
      disabled={busy}
      aria-label={editing?.mode === 'rename' ? '새 이름' : '이름'}
    />
  );

  const menuRow = menu ? entityOf(menu.key) : null;

  return (
    <>
      <div className="page-head page-head--row">
        <div>
          <h1>지역 관리</h1>
          <p>
            기관 {orgs.length}개 · 마을 {villages.length}개. 폴더처럼 다룹니다 — 마디를 고르고 새 기관·새
            마을을 만들고, 끌어서 옮기고, F2 로 이름을 바꿉니다. 주소는 마을에만 적습니다.
          </p>
        </div>
      </div>

      {error && (
        <div className="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      <div className="explorer">
        <aside className="explorer__side card">
          <div className="explorer__head">
            <span className="explorer__title">지역</span>
            <div className="explorer__tools">
              <ToolButton
                title={`새 기관${targetNameForTitle ? ` · ${targetNameForTitle} 아래` : ''}`}
                onClick={() => startCreate('org')}
                disabled={!canNewOrg || busy}
              >
                <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
                  <path
                    d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3.2l1.3 1.5h5.5a1 1 0 0 1 1 1V8h-1.5V6h-10v6.5h5V14h-5a1 1 0 0 1-1-1v-9.5z"
                    fill="currentColor"
                  />
                  <path d="M12.5 9v6M9.5 12h6" stroke="currentColor" strokeWidth="1.6" />
                </svg>
              </ToolButton>
              <ToolButton
                title={`새 마을${targetNameForTitle ? ` · ${targetNameForTitle} 아래` : ''}`}
                onClick={() => startCreate('village')}
                disabled={!canNewVillage || busy}
              >
                <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
                  <path
                    d="M6.5 1.5c-2.5 0-4.5 2-4.5 4.5 0 3.2 4.5 8.5 4.5 8.5s1.1-1.3 2.2-3.1H7.2c-.5-.8-1-1.8-1-2.6A2 2 0 1 1 8.5 6h1.9c.05-.3.1-.7.1-1A4.4 4.4 0 0 0 6.5 1.5z"
                    fill="currentColor"
                  />
                  <path d="M12.5 9v6M9.5 12h6" stroke="currentColor" strokeWidth="1.6" />
                </svg>
              </ToolButton>
              <ToolButton title="모두 펼치기" onClick={expandAll}>
                <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
                  <path d="M3 5.5 8 10l5-4.5" fill="none" stroke="currentColor" strokeWidth="1.7" />
                </svg>
              </ToolButton>
              <ToolButton title="모두 접기" onClick={collapseAll}>
                <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
                  <path d="M3 10.5 8 6l5 4.5" fill="none" stroke="currentColor" strokeWidth="1.7" />
                </svg>
              </ToolButton>
            </div>
          </div>
          <div className="explorer__search">
            <input
              type="search"
              placeholder="이름으로 찾기"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="지역 검색"
            />
          </div>

          <div
            ref={treeRef}
            className="explorer__tree"
            role="tree"
            tabIndex={0}
            onKeyDown={onKeyDown}
            onDragOver={(e) => {
              // 행이 아닌 빈 자리·놓기 영역에서만 — 뿌리(기관) 또는 기관 없음(마을)으로.
              const el = e.target as HTMLElement;
              const onBlank = el === e.currentTarget || el.closest('.explorer__dropzone') !== null;
              if (onBlank && dragRef.current && canDropOn(dragRef.current, { kind: 'root' })) {
                e.preventDefault();
                if (dropKey !== 'root') setDropKey('root');
              }
            }}
            onDrop={(e) => {
              // dragover 와 같은 판정을 다시 한다 — state(dropKey)는 렌더 한 박자 늦을 수 있다.
              const el = e.target as HTMLElement;
              const onBlank = el === e.currentTarget || el.closest('.explorer__dropzone') !== null;
              if (onBlank && dragRef.current && canDropOn(dragRef.current, { kind: 'root' }))
                onDrop(e, { kind: 'root' });
            }}
            onDragLeave={(e) => {
              if (e.currentTarget === e.target) setDropKey(null);
            }}
          >
            {loading ? (
              <div className="empty">불러오는 중…</div>
            ) : rows.length === 0 ? (
              <div className="empty">
                {query
                  ? '검색 결과가 없습니다.'
                  : isSuperAdmin
                    ? '아직 기관이 없습니다. 위의 새 기관 버튼으로 시작하세요.'
                    : '관할 기관이 없습니다. 최고 관리자에게 소속 기관을 요청하세요.'}
              </div>
            ) : (
              rows.map((row) => {
                const isSel = selected?.key === row.key;
                const isEditingThis = editing?.mode === 'rename' && editing.key === row.key;
                const isDrop = dropKey === row.key;
                const isDragging = dragKey === row.key;
                const dropTarget: DropTarget | undefined =
                  row.kind === 'org' && row.orgId !== null
                    ? { kind: 'org', orgId: row.orgId }
                    : row.kind === 'orphans'
                      ? { kind: 'orphans' }
                      : undefined;
                return (
                  <div
                    key={row.key}
                    role="treeitem"
                    aria-selected={isSel}
                    aria-expanded={row.hasChildren ? row.expanded : undefined}
                    aria-level={row.depth + 1}
                    className={`trow trow--${row.kind}${isSel ? ' is-selected' : ''}${isDrop ? ' is-drop' : ''}${isDragging ? ' is-dragging' : ''}`}
                    style={{ ['--depth' as string]: row.depth }}
                    draggable={row.kind === 'org' || row.kind === 'village'}
                    onDragStart={(e) => onDragStart(e, row)}
                    onDragEnd={onDragEnd}
                    onDragOver={(e) => {
                      // 행 위에서는 컨테이너(뿌리 놓기)로 번지지 않게. 마을 행은 놓을 자리가 아니다.
                      e.stopPropagation();
                      if (dropTarget) onDragOver(e, dropTarget, row.key);
                    }}
                    onDragLeave={() => dropKey === row.key && setDropKey(null)}
                    onDrop={(e) => {
                      e.stopPropagation();
                      if (dropTarget) onDrop(e, dropTarget);
                    }}
                    onClick={() => {
                      if (row.kind === 'new') return;
                      setSelected({ key: row.key });
                      treeRef.current?.focus();
                    }}
                    onDoubleClick={() => {
                      if (row.hasChildren) toggle(row.key);
                      else if (row.kind === 'village') startRename(row.key);
                    }}
                    onContextMenu={(e) => onContextMenu(e, row)}
                  >
                    {row.kind === 'org' || row.kind === 'orphans' ? (
                      <button
                        type="button"
                        className="trow__toggle"
                        tabIndex={-1}
                        aria-label={row.expanded ? '접기' : '펼치기'}
                        onClick={(e) => {
                          e.stopPropagation();
                          toggle(row.key);
                        }}
                        style={{ visibility: row.hasChildren ? 'visible' : 'hidden' }}
                      >
                        <Chevron open={row.expanded} />
                      </button>
                    ) : (
                      <span className="trow__toggle" />
                    )}
                    <span className={`trow__icon trow__icon--${row.kind === 'new' ? row.newKind : row.kind}`}>
                      {row.kind === 'village' || (row.kind === 'new' && row.newKind === 'village') ? (
                        <VillageIcon />
                      ) : (
                        <FolderIcon open={row.expanded} />
                      )}
                    </span>
                    {row.kind === 'new' || isEditingThis ? (
                      renderEditInput(row)
                    ) : (
                      <>
                        <span className="trow__name">
                          {row.kind === 'org'
                            ? row.node!.org.name
                            : row.kind === 'village'
                              ? row.village!.name
                              : '기관 없음'}
                        </span>
                        <span className="trow__meta">
                          {row.kind === 'org' && (
                            <>
                              {row.node!.org.village_count > 0 && `마을 ${row.node!.org.village_count}`}
                              {row.node!.org.village_count > 0 && row.node!.org.child_count > 0 && ' · '}
                              {row.node!.org.child_count > 0 && `기관 ${row.node!.org.child_count}`}
                            </>
                          )}
                          {row.kind === 'village' && (
                            <span
                              className={`trow__count${
                                row.village!.device_count > 0 && row.village!.online_count === 0
                                  ? ' is-danger'
                                  : row.village!.online_count > 0
                                    ? ' is-ok'
                                    : ''
                              }`}
                              title={`단말 ${row.village!.device_count}대 · 온라인 ${row.village!.online_count}대`}
                            >
                              {row.village!.online_count}/{row.village!.device_count}
                            </span>
                          )}
                          {row.kind === 'orphans' && `${forest.orphans.length}`}
                        </span>
                      </>
                    )}
                  </div>
                );
              })
            )}
            {dragKey && isSuperAdmin && entityOf(dragKey)?.kind === 'org' && (
              <div className={`explorer__dropzone${dropKey === 'root' ? ' is-drop' : ''}`}>
                여기에 놓으면 최상위 기관이 됩니다
              </div>
            )}
          </div>

          <div className="explorer__legend">
            <kbd>F2</kbd> 이름 · <kbd>Del</kbd> 삭제 · <kbd>←</kbd>
            <kbd>→</kbd> 접기/펼치기 · 끌어서 옮기기 · 우클릭 메뉴
          </div>
        </aside>

        <section className="explorer__detail">
          {!selectedEntity ? (
            <div className="empty explorer__empty">
              <p className="strong">왼쪽에서 기관이나 마을을 고르세요.</p>
              <p className="dim">
                기관은 폴더입니다 — 몇 단이든 만들 수 있고 권한은 이 트리를 따릅니다. 마을은 파일입니다 —
                주소·좌표·구역·단말은 마을에만 있습니다.
              </p>
            </div>
          ) : selectedEntity.kind === 'village' ? (
            <VillagePanel
              village={selectedEntity.village}
              pathLabel={
                selectedEntity.orgId !== null ? pathLabel(forest.byId.get(selectedEntity.orgId)!) : ''
              }
              onChanged={load}
              onDelete={() => void remove(keyOfVillage(selectedEntity.village.id))}
            />
          ) : selectedEntity.kind === 'orphans' ? (
            <div className="empty explorer__empty">
              <p className="strong">기관 없음 · 마을 {forest.orphans.length}개</p>
              <p className="dim">
                아무 기관에도 속하지 않은 마을입니다. 최고 관리자만 봅니다. 기관 아래로 끌어다 놓으면 그 기관
                관리자에게 보이기 시작합니다.
              </p>
            </div>
          ) : (
            <OrgPanel
              node={selectedEntity.node}
              isMyRoot={selectedEntity.orgId === myRootId}
              canDelete={canDeleteOrg(selectedEntity.node)}
              deleteReason={deleteBlockReason(selectedEntity.node)}
              onRename={() => startRename(keyOfOrg(selectedEntity.orgId))}
              onNewOrg={() => startCreate('org')}
              onNewVillage={() => startCreate('village')}
              onDelete={() => void remove(keyOfOrg(selectedEntity.orgId))}
              onSelect={(key) => {
                setSelected({ key });
                toggle(keyOfOrg(selectedEntity.orgId), true);
              }}
            />
          )}
        </section>
      </div>

      {menu && menuRow && (
        <div
          className="ctxmenu"
          style={{ left: menu.x, top: menu.y }}
          role="menu"
          onMouseDown={(e) => e.stopPropagation()}
        >
          {(menuRow.kind === 'org' || menuRow.kind === 'orphans' || menuRow.kind === 'village') && (
            <>
              {(menuRow.kind !== 'orphans' || isSuperAdmin) && (
                <button type="button" role="menuitem" onClick={() => startCreate('org')} disabled={!canNewOrg}>
                  새 기관
                </button>
              )}
              <button type="button" role="menuitem" onClick={() => startCreate('village')} disabled={!canNewVillage}>
                새 마을
              </button>
            </>
          )}
          {(menuRow.kind === 'org' || menuRow.kind === 'village') && (
            <>
              <div className="ctxmenu__sep" />
              <button type="button" role="menuitem" onClick={() => startRename(menu.key)}>
                이름 바꾸기 <kbd>F2</kbd>
              </button>
              <button
                type="button"
                role="menuitem"
                className="is-danger"
                onClick={() => void remove(menu.key)}
                disabled={menuRow.kind === 'org' && !canDeleteOrg(menuRow.node)}
                title={menuRow.kind === 'org' ? deleteBlockReason(menuRow.node) || undefined : undefined}
              >
                삭제 <kbd>Del</kbd>
              </button>
            </>
          )}
        </div>
      )}
    </>
  );
}

// ── 기관 패널 ─────────────────────────────────────────────────────────────
function OrgPanel({
  node,
  isMyRoot,
  canDelete,
  deleteReason,
  onRename,
  onNewOrg,
  onNewVillage,
  onDelete,
  onSelect,
}: {
  node: OrgNode;
  isMyRoot: boolean;
  canDelete: boolean;
  deleteReason: string;
  onRename: () => void;
  onNewOrg: () => void;
  onNewVillage: () => void;
  onDelete: () => void;
  onSelect: (key: RowKey) => void;
}) {
  const stats = subtreeStats(node);
  const path = pathOf(node);
  return (
    <>
      <div className="detail__head">
        <div>
          <div className="crumbs">
            {path.map((n) => (
              <span key={n.org.id} className="crumbs__item">
                {n.org.name}
              </span>
            ))}
          </div>
          <h2 className="detail__title">
            <FolderIcon open />
            {node.org.name}
            {isMyRoot && <span className="tag">내 기관</span>}
          </h2>
        </div>
        <div className="detail__actions">
          <button type="button" className="btn btn--ghost" onClick={onRename}>
            이름 바꾸기
          </button>
          <button type="button" className="btn" onClick={onNewOrg}>
            + 새 기관
          </button>
          <button type="button" className="btn btn--primary" onClick={onNewVillage}>
            + 새 마을
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--danger"
            onClick={onDelete}
            disabled={!canDelete}
            title={canDelete ? undefined : deleteReason}
          >
            삭제
          </button>
        </div>
      </div>

      <div className="tiles tiles--4">
        <Tile label="하위 기관" value={stats.orgs} unit="개" note={`바로 아래 ${node.org.child_count}개`} />
        <Tile label="마을" value={stats.villages} unit="개" note={`바로 아래 ${node.org.village_count}개`} />
        <Tile label="단말" value={stats.devices} unit="대" note="아래 전부 합계" />
        <Tile
          label="온라인"
          value={stats.online}
          unit="대"
          tone={stats.devices > 0 && stats.online === 0 ? 'danger' : stats.online > 0 ? 'ok' : undefined}
        />
      </div>

      <p className="hint">
        이 기관의 관리자 계정 {node.org.user_count}개는 여기 아래 마을 {stats.villages}개를 전부 봅니다. 나중에
        붙이는 마을·기관도 자동으로 들어갑니다.
      </p>

      {node.children.length > 0 && (
        <section className="card detail__card">
          <h3 className="section-title">하위 기관 {node.children.length}</h3>
          <ul className="plain-list plain-list--click">
            {node.children.map((c) => {
              const s = subtreeStats(c);
              return (
                <li key={c.org.id} onClick={() => onSelect(keyOfOrg(c.org.id))}>
                  <span className="strong">
                    <FolderIcon open={false} /> {c.org.name}
                  </span>
                  <span className="dim num">
                    마을 {s.villages} · 단말 {s.online}/{s.devices}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="card detail__card">
        <h3 className="section-title">마을 {node.villages.length}</h3>
        {node.villages.length === 0 ? (
          <div className="empty empty--tight">
            바로 아래에 마을이 없습니다. 「+ 새 마을」로 만들거나 다른 곳의 마을을 끌어다 놓으세요.
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>마을</th>
                  <th>주소</th>
                  <th className="mono">village_id</th>
                  <th className="num">단말</th>
                </tr>
              </thead>
              <tbody>
                {node.villages.map((v) => (
                  <tr key={v.id} className="is-click" onClick={() => onSelect(keyOfVillage(v.id))}>
                    <td className="strong">{v.name}</td>
                    <td className="dim">{v.road_address ?? v.jibun_address ?? ([v.sido, v.sigungu].filter(Boolean).join(' ') || '—')}</td>
                    <td className="mono">{v.village_token}</td>
                    <td className="num">
                      {v.online_count}/{v.device_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
