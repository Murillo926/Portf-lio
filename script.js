
const sections = document.querySelectorAll('.slide');

const observer = new IntersectionObserver((entries, obs) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('show');
      obs.unobserve(entry.target);
    }
  });
}, { threshold: 0.2 });

sections.forEach(sec => observer.observe(sec));


const quotes = document.querySelectorAll('blockquote');
setInterval(() => {
  quotes.forEach(q => q.classList.toggle('pulse'));
}, 2500);


const particles = document.getElementById('particles');

for (let i = 0; i < 60; i++) {
  const span = document.createElement('span');
  span.classList.add('particle');
  span.style.left = Math.random() * 100 + 'vw';
  span.style.top = Math.random() * 100 + 'vh';
  span.style.width = span.style.height = Math.random() * 8 + 4 + 'px';
  span.style.background = Math.random() > 0.5 ? '#00ff9d' : '#ffdf6b';
  span.style.animationDuration = 5 + Math.random() * 12 + 's';
  particles.appendChild(span);
}
