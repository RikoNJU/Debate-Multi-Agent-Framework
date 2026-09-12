export default function PageFooter() {
  return (
    <footer className="app-footer">
      <div className="footer-brand">
        <span className="footer-logo">
          <img src="/assets/rwzp-logo.jpg" alt="睿文智评标志" />
        </span>
        <span>睿文智评</span>
      </div>
      <div className="footer-meta">南京大学 · 本科毕业论文智能评审系统</div>
      <span className="footer-copy">© {new Date().getFullYear()} 睿文智评. 保留所有权利.</span>
    </footer>
  );
}
