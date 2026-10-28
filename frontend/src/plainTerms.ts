// One-line meanings of the stability terms, for readers with no ship knowledge. Static: no AI tokens.

export const CRITERION_MEANING: Record<string, string> = {
  GM0_MIN: "How firmly the ship pushes itself back upright after a small roll.",
  AREA_0_30: "How much effort it takes to roll the ship over to 30°.",
  AREA_0_40: "How much effort it takes to roll the ship over to 40°.",
  AREA_30_40: "How much fight-back the ship still has between 30° and 40° of roll.",
  GZ_AT_30: "How hard the ship pushes back when rolled to 30° or more.",
  ANGLE_GZ_MAX: "How far it can roll before its push-back starts to weaken.",
};

export const STAT_MEANING: Record<string, string> = {
  displacement: "Total weight of the ship and everything on board.",
  draught: "How deep the ship sits in the water at the bow and the stern.",
  trim: "Front-to-back tilt: how much deeper one end sits than the other.",
  list: "Sideways lean. Zero means upright.",
  gm: "Self-righting strength. Bigger means steadier.",
  free: "Strength lost to liquid sloshing in part-full tanks.",
};
