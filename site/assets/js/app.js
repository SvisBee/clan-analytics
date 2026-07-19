document.documentElement.classList.add("js-ready");

const menuButton = document.querySelector(".menu-toggle");
const navigation = document.querySelector("#site-navigation");

if (menuButton && navigation) {
  const closeMenu = () => {
    navigation.classList.remove("is-open");
    menuButton.setAttribute("aria-expanded", "false");
  };

  menuButton.addEventListener("click", () => {
    const isOpen = navigation.classList.toggle("is-open");
    menuButton.setAttribute("aria-expanded", String(isOpen));
  });

  navigation.addEventListener("click", (event) => {
    if (event.target instanceof HTMLAnchorElement) {
      closeMenu();
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && navigation.classList.contains("is-open")) {
      closeMenu();
      menuButton.focus();
    }
  });

  const wideViewport = window.matchMedia("(min-width: 62.01rem)");
  wideViewport.addEventListener("change", (event) => {
    if (event.matches) {
      closeMenu();
    }
  });
}

const navigationLinks = Array.from(
  document.querySelectorAll('.site-nav a[href^="#"]')
);

const setActiveNavigationLink = (activeId) => {
  navigationLinks.forEach((link) => {
    const isActive = link.getAttribute("href") === `#${activeId}`;

    if (isActive) {
      link.setAttribute("aria-current", "location");
    } else {
      link.removeAttribute("aria-current");
    }
  });
};

navigationLinks.forEach((link) => {
  link.addEventListener("click", () => {
    setActiveNavigationLink(link.getAttribute("href").slice(1));
  });
});

if ("IntersectionObserver" in window) {
  const sections = navigationLinks
    .map((link) => document.querySelector(link.getAttribute("href")))
    .filter(Boolean);

  const sectionObserver = new IntersectionObserver(
    (entries) => {
      const visibleSection = entries.find((entry) => entry.isIntersecting);

      if (visibleSection) {
        setActiveNavigationLink(visibleSection.target.id);
      }
    },
    { rootMargin: "-30% 0px -60% 0px" }
  );

  sections.forEach((section) => sectionObserver.observe(section));
}
