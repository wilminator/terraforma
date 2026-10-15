// The command menu of the fight scene: what the player's heroes do this round. The server says what each hero can do (the fight view's
// `menu`: attacks, skills and spells with their cost, items, whether it may flee, and whom each is aimed at) and decides whether a
// command is allowed; this only offers the choices, asks for a target where one is needed and sends the command through the commands
// call. The player commands each of their heroes in turn (the first that has not chosen), may pick another hero by its card to choose
// again, and the round plays when the last has chosen or the round's time runs out.
//
// Modes (the stage's `data-menu`): `commands` (the hero's choices), `list` (skills, spells or items to pick from), `target` (the
// fighters the command can be aimed at light up and are picked by their cards), `waiting` (every hero has chosen), `off` (a round is
// being played, or the fight is over).

import { h, replace } from "./dom.js";

export class FightMenu {
  constructor(scene) {
    this.scene = scene;
    this.t = scene.t;
    this.mode = "off";
    this.hero = null; // the key of the hero being commanded
    this.pick = null; // the command being aimed: { command, using, aim }
    this.list = null; // which list is shown: "skill", "spell" or "item"
    this.busy = false;
    this.problem = "";
    this.element = null;
    this.shown = "";
    this.wired = new WeakSet();
    this.onkey = (event) => {
      if (event.key === "Escape" && (this.mode === "list" || this.mode === "target")) {
        event.preventDefault();
        this.back();
      }
    };
  }

  /** Puts the menu in the fight layer. */
  attach(layer) {
    this.element = h("div", { class: "fight-menu", id: "fight-menu", role: "group", "aria-label": this.t("fight.menu.label"), hidden: true });
    layer.append(this.element);
    this.mode = "off";
    this.shown = "";
    document.removeEventListener("keydown", this.onkey);
    document.addEventListener("keydown", this.onkey);
  }

  detach() {
    document.removeEventListener("keydown", this.onkey);
    this.element = null;
    this.mode = "off";
    this.hero = null;
    this.pick = null;
    this.list = null;
    this.busy = false;
    this.problem = "";
    this.shown = "";
  }

  get model() {
    return this.scene.model;
  }

  /** The player's heroes that can be commanded, as [key, fighter]. */
  #heroes() {
    return [...this.model.fighters].filter(([, each]) => each.yours && each.alive && !each.fled && each.menu);
  }

  /** Works out what the menu shows from the fight as it stands, and redraws it if that changed. Called whenever the scene paints. */
  sync() {
    if (!this.element || !this.model) return;
    const scene = this.scene;
    const heroes = scene.playing || scene.queue.length || this.model.over ? [] : this.#heroes();
    if (!heroes.length) {
      this.mode = "off";
      this.hero = null;
    } else {
      const keys = heroes.map(([key]) => key);
      if (!keys.includes(this.hero)) {
        this.hero = keys.find((key) => !this.model.fighter(key).committed) ?? null;
        this.#to(this.hero ? "commands" : "waiting");
      } else if (this.mode === "off" || this.mode === "waiting") {
        this.mode = "commands";
      }
    }
    this.#targets();
    const hero = this.hero ? this.model.fighter(this.hero) : null;
    const signature = JSON.stringify([this.mode, this.hero, this.list, this.pick, this.busy, this.problem, hero?.menu, hero ? Object.entries(hero.resources) : null]);
    if (signature === this.shown) return;
    this.shown = signature;
    this.#draw(hero);
  }

  #to(mode) {
    this.mode = mode;
    this.pick = null;
    this.list = null;
    this.problem = "";
  }

  /** Marks the fighters that can be picked as the target, and the player's own heroes that can be picked to be commanded. */
  #targets() {
    const aimed = this.mode === "target" ? new Set(this.model.targets(this.hero, this.pick.aim)) : new Set();
    const choosable = this.mode === "off" ? new Set() : new Set(this.#heroes().map(([key]) => key));
    for (const [key, card] of this.scene.cards) {
      if (!this.wired.has(card)) {
        this.wired.add(card);
        card.addEventListener("click", () => this.#picked(key));
        card.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            this.#picked(key);
          }
        });
      }
      const fighter = this.model.fighter(key);
      const targetable = aimed.has(key);
      const own = !targetable && choosable.has(key) && key !== this.hero && this.mode !== "target";
      card.dataset.targetable = String(targetable);
      card.dataset.commanded = String(key === this.hero && this.mode !== "off");
      if (targetable) {
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("aria-label", this.t("fight.menu.target_label", { name: fighter.name }));
      } else if (own) {
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("aria-label", fighter.name);
      } else {
        card.removeAttribute("role");
        card.removeAttribute("tabindex");
        card.removeAttribute("aria-label");
      }
    }
  }

  #picked(key) {
    if (this.busy || !this.model) return;
    if (this.mode === "target") {
      if (this.model.targets(this.hero, this.pick.aim).includes(key)) this.#send(this.pick, key);
      return;
    }
    const chosen = this.#heroes().some(([each]) => each === key);
    if (chosen && key !== this.hero && this.mode !== "off") {
      this.hero = key;
      this.#to("commands");
      this.scene.repaint();
    }
  }

  back() {
    const list = this.mode === "target" ? this.pick.list : null;
    this.#to(list ? "list" : "commands");
    this.list = list;
    this.scene.repaint();
  }

  // --- choosing ------------------------------------------------------------------------------------

  /** A command from the menu: aimed at someone, or at once if it takes no aim. */
  #choose(command, using, aim, list = null) {
    if (aim === "none") {
      this.#send({ command, using, aim }, this.hero);
      return;
    }
    this.pick = { command, using, aim, list };
    this.mode = "target";
    this.scene.repaint();
  }

  #open(list) {
    this.mode = "list";
    this.list = list;
    this.scene.repaint();
  }

  async #send({ command, using }, target) {
    const scene = this.scene;
    const model = this.model;
    const round = model.round;
    const hero = this.hero;
    const address = (key) => {
      const [party, group, character] = key.split(".").map(Number);
      return { party, group, character };
    };
    this.busy = true;
    this.problem = "";
    scene.repaint();
    try {
      await scene.source.command(model.id, { fighter: address(hero), command, using, target: address(target) });
      if (this.model === model && model.round === round) model.fighter(hero).committed = true;
      if (this.model === model) this.#to("commands");
      if (this.model === model) this.hero = null;
    } catch (error) {
      this.#to("commands");
      this.problem = error.message;
      await scene.refreshMenu();
    } finally {
      this.busy = false;
      if (this.element) scene.repaint();
    }
  }

  // --- drawing -------------------------------------------------------------------------------------

  #draw(hero) {
    const layer = this.scene.layer;
    if (!layer || !this.element) return;
    const off = this.mode === "off";
    layer.classList.toggle("asking", !off);
    this.element.hidden = off;
    this.scene.stage.dataset.menu = this.mode;
    if (this.hero) this.scene.stage.dataset.hero = this.hero;
    else delete this.scene.stage.dataset.hero;
    if (off) {
      replace(this.element);
      return;
    }
    const head = h(
      "div",
      { class: "menu-head" },
      h("span", { class: "menu-hero", id: "fight-menu-hero" }, hero ? this.t("fight.menu.hero", { name: hero.name }) : ""),
      this.mode === "target" ? h("span", { class: "menu-prompt", id: "fight-menu-prompt" }, this.t("fight.menu.target")) : null,
      this.busy ? h("span", { class: "menu-prompt" }, this.t("fight.menu.sending")) : null,
      this.problem ? h("span", { class: "menu-problem", role: "alert" }, this.problem) : null,
    );
    let body;
    if (this.mode === "waiting") body = h("p", { class: "menu-wait" }, this.t("fight.menu.waiting"));
    else if (this.mode === "list") body = this.#list(hero);
    else if (this.mode === "target") body = h("div", { class: "menu-buttons" }, this.#back());
    else body = this.#commands(hero);
    replace(this.element, head, body);
    this.#focus();
  }

  #back() {
    return h("button", { type: "button", id: "menu-back", onclick: () => this.back() }, this.t("fight.menu.back"));
  }

  #commands(hero) {
    const menu = hero.menu;
    const button = (command, label, onclick, disabled = false) =>
      h("button", { type: "button", class: "menu-command", "data-command": command, disabled: disabled || this.busy, onclick }, label);
    const buttons = menu.attacks.map((attack) =>
      button(
        attack.command,
        menu.attacks.length > 1 && attack.weapon ? this.t("fight.menu.attack_with", { weapon: attack.weapon }) : this.t("fight.menu.attack"),
        () => this.#choose(attack.command, 0, attack.aim),
      ),
    );
    const kinds = { skill: menu.abilities.filter((each) => each.kind === "skill"), spell: menu.abilities.filter((each) => each.kind === "spell"), item: menu.items };
    for (const kind of ["skill", "spell", "item"]) buttons.push(button(kind, this.t(`fight.menu.${kind}`), () => this.#open(kind), kinds[kind].length === 0));
    buttons.push(button("defend", this.t("fight.menu.defend"), () => this.#send({ command: "defend", using: 0 }, this.hero)));
    buttons.push(button("flee", this.t("fight.menu.flee"), () => this.#send({ command: "flee", using: 0 }, this.hero), !menu.can_flee));
    return h("div", { class: "menu-buttons" }, buttons);
  }

  #list(hero) {
    const menu = hero.menu;
    let choices;
    if (this.list === "item") {
      choices = menu.items.map((each) => ({ label: this.t("fight.menu.quantity", { name: each.name, quantity: each.quantity }), ok: true, command: "item", each }));
    } else {
      choices = menu.abilities
        .filter((each) => each.kind === this.list)
        .map((each) => {
          const have = hero.resources[each.resource]?.[0] ?? 0;
          return { label: each.cost ? this.t("fight.menu.cost", { name: each.name, cost: each.cost, resource: each.resource }) : each.name, ok: have >= each.cost, command: each.kind, each };
        });
    }
    const buttons = choices.map(({ label, ok, command, each }) =>
      h("button", { type: "button", class: "menu-choice", "data-using": each.index, disabled: !ok || this.busy, onclick: () => this.#choose(command, each.index, each.aim, this.list) }, label),
    );
    return h("div", { class: "menu-buttons" }, buttons.length ? buttons : h("span", { class: "menu-wait" }, this.t("fight.menu.nothing")), this.#back());
  }

  /** Keeps the keyboard where the player is working: on the menu's first choice, or on the first fighter that can be aimed at. */
  #focus() {
    const active = document.activeElement;
    if (active && active !== document.body && !this.scene.layer.contains(active)) return;
    const first = this.mode === "target" ? this.scene.layer.querySelector(".fighter[data-targetable='true']") : this.element.querySelector("button:not([disabled])");
    first?.focus();
  }
}
