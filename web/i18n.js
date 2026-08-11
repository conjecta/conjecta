(function () {
  const STORAGE_KEY = "conjecta-lang";

  const messages = {
    en: {
      "nav.primary": "Primary",
      "nav.home": "Home",
      "nav.app": "App",
      "nav.support": "Contact support",
      "lang.switch": "Language",
      "home.epigraph": "Rigor begins with a conjecture.",
      "home.kicker": "A workspace for mathematical reasoning",
      "home.lead":
        "Turn a mathematical question into a line of reasoning you can inspect.",
      "home.intro":
        "Plan a route, explore useful lemmas, call computation and formal tools, then keep the decisive steps visible.",
      "home.try": "Open the workbench",
      "home.seeMethod": "See how it works",
      "home.scroll": "Follow the reasoning",
      "home.phrase.aria": "Quiet phrases",
      "home.scene.aria":
        "An example proof path showing that the square root of two is irrational",
      "home.scene.status": "Reasoning trace",
      "home.scene.proposition": "Proposition",
      "home.scene.assume": "Assume",
      "home.scene.strategy": "parity descent",
      "home.scene.derive": "Derive",
      "home.scene.verify": "Check",
      "home.scene.result": "Contradiction found",
      "home.thesis.kicker": "What Conjecta changes",
      "home.thesis.title": "Not just an answer. A route you can inspect.",
      "home.thesis.body":
        "Difficult mathematics rarely moves in a straight line. Conjecta keeps exploration, verification, and revision in one visible process—so the useful artifact is the reasoning, not only the final sentence.",
      "home.thesis.stage1": "Question",
      "home.thesis.stage2": "Strategy",
      "home.thesis.stage3": "Lemmas",
      "home.thesis.stage4": "Verification",
      "home.method.kicker": "One continuous workspace",
      "home.method.title": "From first question to checked result",
      "home.method.intro":
        "You set the problem and remain in control. Conjecta makes the intermediate work legible as the route develops.",
      "home.method.step1.title": "State the problem",
      "home.method.step1.body":
        "Describe the theorem, attach context, and mark what a satisfactory result must establish.",
      "home.method.step2.title": "Build a proof route",
      "home.method.step2.body":
        "Break the goal into lemmas, compare strategies, and revise when a branch stops paying off.",
      "home.method.step3.title": "Check decisive steps",
      "home.method.step3.body":
        "Use computation, retrieval, structural checks, or Lean where the argument needs firmer ground.",
      "home.method.step4.title": "Keep the useful trail",
      "home.method.step4.body":
        "Review the route, continue the same project, and retain context worth reusing in later work.",
      "home.capabilities.kicker": "Designed for serious problems",
      "home.capabilities.title": "The work stays visible.",
      "home.capabilities.intro":
        "Each layer answers a different question: where to go, what to trust, and what to carry forward.",
      "home.capabilities.explore.title": "Strategy is not a black box",
      "home.capabilities.explore.body":
        "See the working plan, candidate lemmas, alternative branches, and the reason the system changes direction.",
      "home.capabilities.verify.title":
        "Verification lives inside the process",
      "home.capabilities.verify.body":
        "Computation and formal tooling can test the steps that matter, with their outcomes kept beside the argument.",
      "home.capabilities.remember.title": "Context can accumulate",
      "home.capabilities.remember.body":
        "Projects preserve the conversation and the proof state, while reusable knowledge can support the next problem.",
      "home.cta.kicker": "Begin with the question you already have",
      "home.cta.title": "Give the conjecture room to become an argument.",
      "home.cta.button": "Start reasoning",
      "home.contact.kicker": "We read every message",
      "home.contact.title": "Contact support",
      "home.contact.intro":
        "Tell us what you need help with. Leave your email and preferred name so we can reply.",
      "home.contact.name": "Preferred name",
      "home.contact.namePlaceholder": "How should we address you?",
      "home.contact.email": "Email",
      "home.contact.emailPlaceholder": "you@example.com",
      "home.contact.message": "Message",
      "home.contact.messagePlaceholder": "What would you like help with?",
      "home.contact.send": "Send message",
      "home.contact.sending": "Sending…",
      "home.contact.success": "Thanks — your message was sent.",
      "home.contact.error": "Could not send right now. Please try again.",
      "home.footer.note":
        "Rigorous mathematical reasoning, made inspectable.",
      "footer.terms": "Terms",
      "docs.arch": "Architecture & agent design",
      "docs.data": "Data & domain packs",
      "docs.repo": "Repository",
      "docs.architecture.title": "Architecture overview",
      "docs.architecture.desc":
        "Components, verification policy, near-term integrations",
      "docs.agent.title": "Agent framework",
      "docs.agent.desc": "Agent loop, regulators, and orchestration design",
      "docs.dag.title": "Proof DAG agent",
      "docs.dag.desc": "Lemma DAG ensemble generation and scheduling",
      "docs.skills.title": "Agent skill registry",
      "docs.skills.desc":
        "Proof-state skill routing and progressive disclosure",
      "docs.survey.title": "Research survey",
      "docs.survey.desc": "Background literature and related systems",
      "docs.database.title": "Database and knowledge design",
      "docs.database.desc": "Indexes, retrieval, and storage plans",
      "docs.talagrand.title": "Talagrand domain pack",
      "docs.talagrand.desc": "Local benchmark pack from LaTeX/PDF bundles",
      "docs.review.title": "Talagrand math review brief",
      "docs.review.desc": "Statement review task specification",
      "docs.source.title": "Source repository",
      "docs.source.desc": "CLI, formalizations, evaluation reports, and tests",
      "docs.packs.title": "Domain packs",
      "docs.packs.desc": "Proof targets, semantic profiles, and source metadata",
      "docs.formal.title": "Formalizations",
      "docs.formal.desc": "Lean scaffolds and candidate artifacts",
      "terms.title": "Terms & disclaimers",
      "terms.intro":
        "Important limitations for public use, third-party review, and external integrations.",
      "terms.research.title": "Research software",
      "terms.research.body":
        "Conjecta is experimental research software. APIs, CLI commands, evaluation reports, and formalization scaffolds may change without notice. Do not rely on this project for production proof certification without independent review.",
      "terms.correctness.title": "No guarantee of correctness",
      "terms.correctness.body":
        "Lean gates, structural verifiers, and evaluation harnesses reduce certain classes of errors but do not guarantee mathematical correctness of every statement or proof artifact. Human review remains required for paper-level claims.",
      "terms.sorry.title": "Placeholders and sorry",
      "terms.sorry.body":
        "Candidates containing placeholder axioms, <code>sorry</code>, or unapproved <code>axiom</code> declarations are not accepted as completed proofs. Reports may still list them for review and benchmarking.",
      "terms.thirdparty.title": "Third-party content",
      "terms.thirdparty.body":
        "Domain packs may ingest LaTeX, PDF, and zip bundles from contributors. Copyright and licensing of source materials remain with their original authors. Check each domain pack manifest before redistributing extracted content.",
      "terms.external.title": "External services",
      "terms.external.body":
        "Conjecta sends prompts to third-party LLM providers under their respective terms. API keys and usage costs are provided by the service operator; users do not need to supply their own keys.",
      "terms.liability.title": "Liability",
      "terms.liability.body":
        "This project is provided \u201cas is\u201d, without warranty of any kind. Contributors and maintainers are not liable for damages arising from use of this software or its outputs.",
      "terms.skeleton":
        "License identifier and data-retention notes — to be added.",
      "page.home": "Conjecta",
      "page.terms": "Terms — Conjecta",
      "meta.home":
        "A workspace for mathematical reasoning, proof exploration, and verification.",
      "meta.terms": "Conjecta terms and disclaimers.",
    },
    zh: {
      "nav.primary": "主导航",
      "nav.home": "首页",
      "nav.app": "应用",
      "nav.support": "联系支持",
      "lang.switch": "语言",
      "home.epigraph": "严谨，始于猜想。",
      "home.kicker": "数学推理工作台",
      "home.lead": "把一个数学问题，展开成一条可检查的推理路径。",
      "home.intro":
        "规划证明路线，探索有用引理，调用计算与形式化工具，并始终保留决定性的中间步骤。",
      "home.try": "进入工作台",
      "home.seeMethod": "了解工作方式",
      "home.scroll": "沿着推理向下",
      "home.phrase.aria": "静默短句",
      "home.scene.aria": "证明根号二为无理数的示例推理路径",
      "home.scene.status": "推理轨迹",
      "home.scene.proposition": "命题",
      "home.scene.assume": "假设",
      "home.scene.strategy": "奇偶递降",
      "home.scene.derive": "推出",
      "home.scene.verify": "检查",
      "home.scene.result": "得到矛盾",
      "home.thesis.kicker": "Conjecta 改变了什么",
      "home.thesis.title": "不只给出答案，也交付一条可检查的路径。",
      "home.thesis.body":
        "困难的数学问题很少沿直线前进。Conjecta 把探索、验证与修订放在同一个可见过程里——真正有用的产物不仅是最终结论，更是通往结论的推理。",
      "home.thesis.stage1": "问题",
      "home.thesis.stage2": "策略",
      "home.thesis.stage3": "引理",
      "home.thesis.stage4": "验证",
      "home.method.kicker": "一处连续的工作空间",
      "home.method.title": "从最初的问题，到经检查的结果",
      "home.method.intro":
        "你定义问题，也始终掌握方向。随着路线展开，Conjecta 会让所有中间工作保持清晰可读。",
      "home.method.step1.title": "说明问题",
      "home.method.step1.body":
        "描述需要证明的命题，附上相关材料，并明确一个合格结果必须回答什么。",
      "home.method.step2.title": "搭建证明路线",
      "home.method.step2.body":
        "把目标拆成引理，比较不同策略，并在某条分支失去价值时及时修订。",
      "home.method.step3.title": "检查关键步骤",
      "home.method.step3.body":
        "在论证需要更坚实依据的地方，使用计算、检索、结构检查或 Lean。",
      "home.method.step4.title": "保留有用轨迹",
      "home.method.step4.body":
        "回看完整路线，在同一项目中继续工作，并留下值得在后续问题中复用的上下文。",
      "home.capabilities.kicker": "为严肃问题而设计",
      "home.capabilities.title": "思考过程，始终可见。",
      "home.capabilities.intro":
        "每一层都回答一个不同的问题：往哪里走、什么可信，以及什么值得带到下一次工作中。",
      "home.capabilities.explore.title": "策略不是黑箱",
      "home.capabilities.explore.body":
        "查看正在执行的计划、候选引理、替代分支，以及系统改变方向的原因。",
      "home.capabilities.verify.title": "验证嵌入推理过程",
      "home.capabilities.verify.body":
        "计算与形式化工具可以检查真正关键的步骤，检查结果会和论证本身放在一起。",
      "home.capabilities.remember.title": "上下文可以累积",
      "home.capabilities.remember.body":
        "项目会保留对话与证明状态，可复用的知识也能继续支持下一个问题。",
      "home.cta.kicker": "从你已经在思考的问题开始",
      "home.cta.title": "给猜想足够的空间，让它成为论证。",
      "home.cta.button": "开始推理",
      "home.contact.kicker": "我们会阅读每一条留言",
      "home.contact.title": "联系支持",
      "home.contact.intro":
        "告诉我们你需要什么帮助。留下邮箱与称呼，方便我们回复。",
      "home.contact.name": "称呼",
      "home.contact.namePlaceholder": "我们该如何称呼你？",
      "home.contact.email": "邮箱",
      "home.contact.emailPlaceholder": "you@example.com",
      "home.contact.message": "留言",
      "home.contact.messagePlaceholder": "你希望我们帮什么？",
      "home.contact.send": "发送留言",
      "home.contact.sending": "发送中…",
      "home.contact.success": "已收到，我们会尽快回复。",
      "home.contact.error": "暂时无法发送，请稍后再试。",
      "home.footer.note": "严谨的数学推理，也应该能够被检查。",
      "footer.terms": "条款",
      "docs.arch": "架构与智能体设计",
      "docs.data": "数据与领域包",
      "docs.repo": "代码仓库",
      "docs.architecture.title": "架构概览",
      "docs.architecture.desc": "组件、验证策略与近期集成",
      "docs.agent.title": "智能体框架",
      "docs.agent.desc": "智能体循环、调节器与编排设计",
      "docs.dag.title": "证明 DAG 智能体",
      "docs.dag.desc": "引理 DAG 集成生成与调度",
      "docs.skills.title": "智能体技能注册",
      "docs.skills.desc": "证明状态技能路由与渐进披露",
      "docs.survey.title": "研究综述",
      "docs.survey.desc": "背景文献与相关系统",
      "docs.database.title": "数据库与知识设计",
      "docs.database.desc": "索引、检索与存储规划",
      "docs.talagrand.title": "Talagrand 领域包",
      "docs.talagrand.desc": "由 LaTeX/PDF 包构建的本地基准包",
      "docs.review.title": "Talagrand 数学评审简报",
      "docs.review.desc": "陈述评审任务说明",
      "docs.source.title": "源代码仓库",
      "docs.source.desc": "CLI、形式化、评测报告与测试",
      "docs.packs.title": "领域包",
      "docs.packs.desc": "证明目标、语义配置与源数据",
      "docs.formal.title": "形式化",
      "docs.formal.desc": "Lean 脚手架与候选产物",
      "terms.title": "条款与免责声明",
      "terms.intro":
        "面向公开使用、第三方评审与外部集成的重要限制。",
      "terms.research.title": "研究软件",
      "terms.research.body":
        "Conjecta 为实验性研究软件。API、CLI 命令、评测报告与形式化脚手架可能随时变更。请勿在未独立评审的情况下将其用于生产级证明认证。",
      "terms.correctness.title": "不保证正确性",
      "terms.correctness.body":
        "Lean 门禁、结构验证器与评测框架可降低部分错误风险，但不保证每条陈述或证明产物的数学正确性。论文级主张仍需人工评审。",
      "terms.sorry.title": "占位符与 sorry",
      "terms.sorry.body":
        "包含占位公理、<code>sorry</code> 或未批准 <code>axiom</code> 声明的候选不被视为已完成证明。报告仍可能列出它们以供评审与基准测试。",
      "terms.thirdparty.title": "第三方内容",
      "terms.thirdparty.body":
        "领域包可能收录贡献者提供的 LaTeX、PDF 与 zip 包。源材料版权与许可仍归原作者。再分发提取内容前请查阅各领域包清单。",
      "terms.external.title": "外部服务",
      "terms.external.body":
        "Conjecta 会将提示发送至第三方 LLM 服务商，适用其各自条款。API 密钥与使用成本由服务运营方提供，用户无需自备密钥。",
      "terms.liability.title": "责任限制",
      "terms.liability.body":
        "本项目按「原样」提供，不作任何担保。贡献者与维护者不对使用本软件或其输出所造成的损害承担责任。",
      "terms.skeleton": "许可标识与数据保留说明 — 待补充。",
      "page.home": "Conjecta",
      "page.terms": "条款 — Conjecta",
      "meta.home": "用于数学推理、证明探索与验证的工作空间。",
      "meta.terms": "Conjecta 条款与免责声明。",
    },
  };

  function normalizeLang(lang) {
    return lang === "zh" ? "zh" : "en";
  }

  function getLang() {
    return normalizeLang(localStorage.getItem(STORAGE_KEY) || "en");
  }

  function setLang(lang) {
    const normalized = normalizeLang(lang);
    localStorage.setItem(STORAGE_KEY, normalized);
    applyLang(normalized);
    document.dispatchEvent(
      new CustomEvent("conjecta:langchange", { detail: { lang: normalized } }),
    );
  }

  function t(key, lang) {
    const bucket = messages[normalizeLang(lang || getLang())];
    return bucket[key] ?? messages.en[key] ?? "";
  }

  function applyLang(lang) {
    const normalized = normalizeLang(lang);
    document.documentElement.lang = normalized === "zh" ? "zh-CN" : "en";

    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = t(key, normalized);
    });

    document.querySelectorAll("[data-i18n-html]").forEach((el) => {
      const key = el.getAttribute("data-i18n-html");
      if (key) el.innerHTML = t(key, normalized);
    });

    document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
      const key = el.getAttribute("data-i18n-aria");
      if (key) el.setAttribute("aria-label", t(key, normalized));
    });

    document.querySelectorAll("[data-i18n-alt]").forEach((el) => {
      const key = el.getAttribute("data-i18n-alt");
      if (key) el.setAttribute("alt", t(key, normalized));
    });

    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const key = el.getAttribute("data-i18n-placeholder");
      if (key) el.setAttribute("placeholder", t(key, normalized));
    });

    const pageKey = document.body.getAttribute("data-page");
    if (pageKey) {
      document.title = t(`page.${pageKey}`, normalized);
      const meta = document.querySelector('meta[name="description"]');
      if (meta) meta.setAttribute("content", t(`meta.${pageKey}`, normalized));
    }

    document.querySelectorAll(".lang-btn").forEach((btn) => {
      const btnLang = btn.getAttribute("data-lang");
      const active = btnLang === normalized;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });

    document.dispatchEvent(
      new CustomEvent("conjecta:lang", { detail: { lang: normalized } }),
    );
  }

  function initLangSwitch() {
    const group = document.querySelector(".lang-switch");
    if (!group) return;

    group.setAttribute(
      "aria-label",
      t("lang.switch", getLang()),
    );

    group.querySelectorAll(".lang-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        setLang(btn.getAttribute("data-lang"));
        group.setAttribute("aria-label", t("lang.switch", getLang()));
      });
    });
  }

  function init() {
    applyLang(getLang());
    initLangSwitch();
  }

  window.ConjectaI18n = { getLang, setLang, t, applyLang };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
