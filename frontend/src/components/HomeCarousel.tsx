import { useEffect, useState } from 'react';

/**
 * 首页轮播图，顺序即播放顺序。
 * 文件位于 frontend/public/assets/，这里的路径对应 /assets/hero-N.jpg。
 * hero-N.jpg 由未压缩原图 1-6（本地 frontend/_source-images/，不纳入版本控制）压缩而来。
 */
const SLIDES = [
  '/assets/hero-1.jpg',
  '/assets/hero-2.jpg',
  '/assets/hero-3.jpg',
  '/assets/hero-4.jpg',
  '/assets/hero-5.jpg',
  '/assets/hero-6.jpg',
].map((src, index) => ({
  src,
  alt: `系统预览图 ${index + 1}`,
}));

const INTERVAL_MS = 5000;

/** 首页英雄区右上角轮播：每 5 秒自动切换，可点击圆点跳转。 */
export default function HomeCarousel() {
  const [active, setActive] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActive((current) => (current + 1) % SLIDES.length);
    }, INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [active]);

  return (
    <div className="hero-carousel" role="region" aria-label="系统界面预览">
      <div className="hero-carousel-frame">
        {SLIDES.map((slide, index) => (
          <img
            key={slide.src}
            src={slide.src}
            alt={slide.alt}
            className={index === active ? 'is-active' : undefined}
            aria-hidden={index === active ? undefined : true}
            decoding="async"
            draggable={false}
          />
        ))}
      </div>

      <div className="hero-carousel-dots">
        {SLIDES.map((slide, index) => (
          <button
            key={slide.src}
            type="button"
            className={index === active ? 'is-active' : undefined}
            aria-label={`查看第 ${index + 1} 张预览图`}
            aria-current={index === active}
            onClick={() => setActive(index)}
          />
        ))}
      </div>
    </div>
  );
}
