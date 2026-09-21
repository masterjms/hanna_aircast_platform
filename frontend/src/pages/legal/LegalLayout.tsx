/**
 * 약관·개인정보 처리방침의 공통 틀. 로그인 전에도 열려야 하므로(처리방침은 첫 화면에서
 * 바로 갈 수 있어야 한다) 앱 껍데기 밖에서 혼자 선다. 긴 글이라 이 화면만은 위아래로 읽는다.
 */

import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { Logo } from '../../components/Logo';
import { COMPANY, COPYRIGHT } from '../../lib/company';

export function LegalLayout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="legal">
      <header className="legal__top">
        <Link to="/" aria-label="처음 화면으로">
          <Logo size={24} />
        </Link>
        <nav className="legal__nav">
          <Link to="/terms">이용약관</Link>
          <Link to="/privacy">개인정보 처리방침</Link>
          <Link to="/" className="legal__back">
            ← 돌아가기
          </Link>
        </nav>
      </header>
      <article className="legal__doc">
        <h1>{title}</h1>
        <p className="legal__meta">
          {COMPANY.service} · 시행일 {COMPANY.effectiveDate}
        </p>
        {children}
      </article>
      <footer className="legal__foot">{COPYRIGHT}</footer>
    </div>
  );
}

/** 앱 곳곳에 넣는 한 줄 — 저작권 표기와 두 문서로 가는 길. */
export function LegalLinks({ className }: { className?: string }) {
  return (
    <div className={`legal-links${className ? ` ${className}` : ''}`}>
      <div className="legal-links__row">
        <Link to="/terms">이용약관</Link>
        <span aria-hidden="true">·</span>
        <Link to="/privacy" className="legal-links__privacy">
          개인정보 처리방침
        </Link>
      </div>
      <div className="legal-links__copy">{COPYRIGHT}</div>
    </div>
  );
}
