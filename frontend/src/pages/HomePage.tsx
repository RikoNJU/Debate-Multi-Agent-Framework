import { motion } from 'framer-motion';
import { GraduationCap, ShieldCheck, Users } from 'lucide-react';

const values = [
  {
    title: '为学生',
    description: '一键预审，即时获取可落地修改建议与精准评分参考',
    icon: GraduationCap,
  },
  {
    title: '为教师',
    description: '减负机械评审，专注深度学术指导与人工终审',
    icon: Users,
  },
  {
    title: '为教务',
    description: '数据化管理，全流程高效管控与质量追溯',
    icon: ShieldCheck,
  },
];

export default function HomePage() {
  return (
    <div className="home-page">
      <main className="home-main">
        <section className="hero">
          <motion.div
            className="hero-copy"
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.8 }}
          >
            <h1>睿文智评</h1>
            <p>基于多智能体辩论的本科毕业论文智能评审系统</p>
          </motion.div>

          <motion.div
            className="hero-image"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.8, delay: 0.2 }}
          >
            <img src="/assets/system-hero.png" alt="系统架构" />
          </motion.div>
        </section>

        <section className="core-values">
          <div className="core-values-head">
            <h2>核心价值</h2>
            <div className="core-accent" />
          </div>

          <div className="core-values-grid">
            {values.map((item, index) => (
              <motion.div
                key={item.title}
                className="value-card"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: index * 0.1 }}
              >
                <div className="value-icon"><item.icon size={24} /></div>
                <h3>{item.title}</h3>
                <p>{item.description}</p>
              </motion.div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
