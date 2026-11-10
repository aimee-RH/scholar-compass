// The public preview intentionally uses fictional, browser-local data.
const sampleAdvisors = [
  {name:'Ava Morgan',focus:'Trustworthy AI and human-centered machine learning',topics:['ai','machine learning','trustworthy','fairness','explainability','human'],approach:'Evaluates how people understand and rely on AI systems.'},
  {name:'Leo Park',focus:'Computer vision for healthcare',topics:['computer vision','vision','healthcare','medical','imaging','ai'],approach:'Studies visual models for medical images and clinical workflows.'},
  {name:'Nina Solis',focus:'Robotics and embodied learning',topics:['robotics','robot','embodied','reinforcement learning','control','ai'],approach:'Builds learning methods for robots that act in changing environments.'},
  {name:'Omar Reed',focus:'Graph learning and scientific discovery',topics:['graph','knowledge graph','scientific','discovery','network','machine learning'],approach:'Connects scientific entities with graph models to explore new questions.'}
];

function previewAnswer(message, history) {
  const query = message.toLowerCase();
  let named = sampleAdvisors.filter((advisor) => query.includes(advisor.name.toLowerCase()));
  if (!named.length && /\b(first|second|third|fourth)\b/.test(query)) {
    const previous = [...history].reverse().find((item) => item.role === 'assistant')?.content || '';
    const number = ['first','second','third','fourth'].indexOf(query.match(/\b(first|second|third|fourth)\b/)[0]);
    const listed = [...previous.matchAll(/^\d+\. ([A-Z][a-z]+ [A-Z][a-z]+)/gm)].map((item) => item[1]);
    named = sampleAdvisors.filter((advisor) => advisor.name === listed[number]);
  }
  const ranked = sampleAdvisors.map((advisor) => ({advisor,score:advisor.topics.filter((topic) => query.includes(topic)).length})).sort((a,b) => b.score-a.score);
  const advisors = named.length ? named.slice(0,2) : ranked.filter((item) => item.score && item.score === ranked[0].score).slice(0,2).map((item) => item.advisor);
  let text;
  if (named.length >= 2) {
    text = 'Illustrative comparison (fictional profiles):\n' + advisors.map((advisor) => `• ${advisor.name} — ${advisor.focus}. ${advisor.approach}`).join('\n') + '\nChoose based on the research question and methods you want to explore.';
  } else if (named.length) {
    const advisor = advisors[0];
    text = `Illustrative profile — ${advisor.name} (fictional)\nFocus: ${advisor.focus}.\nApproach: ${advisor.approach}\nIn the full product, this would link to verified publications and graph evidence.`;
  } else if (advisors.length) {
    text = 'Illustrative matches (fictional profiles):\n' + advisors.map((advisor,index) => `${index+1}. ${advisor.name} — ${advisor.focus}. ${advisor.approach}`).join('\n') + '\nAsk about a name or say “tell me about the first one”.';
  } else {
    text = 'Try a research interest such as trustworthy AI, computer vision in healthcare, robotics, or graph learning. You can also compare Ava Morgan and Leo Park. All profiles in this preview are fictional.';
  }
  return {message:text,advisors:advisors.map(({name,focus,approach}) => ({name,focus,approach}))};
}

async function demoFetch(_url, options) {
  const data = JSON.parse(options.body);
  if (typeof data.message !== 'string' || !data.message.trim() || data.message.length > 500) {
    return {ok:false,json:async() => ({success:false,error:'Enter a message of 1–500 characters.'})};
  }
  const answer = previewAnswer(data.message.trim(), Array.isArray(data.history) ? data.history.slice(-12) : []);
  return {ok:true,json:async() => ({success:true,...answer,session_id:data.session_id || crypto.randomUUID(),demo:true})};
}
