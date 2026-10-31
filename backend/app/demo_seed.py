"""Seed the demo account's workspace once: one fictional ship and one approved sample plan."""
from . import config, memory, vessels
from .schemas import ConstraintsIn, LayoutTank, MemoryOp, Parcel, VesselLayout
from .stowage import pipeline
from .vessel_builder import build_vessel

SAMPLE_ORDER = "Load Rotterdam: 20,000 mt ULSD, 12,000 mt Jet A-1. Discharge ULSD at Le Havre, Jet at Bilbao."
SAMPLE_PARCELS = [
    Parcel(grade="ULSD", quantity_t=20000, load_port="Rotterdam", discharge_port="Le Havre"),
    Parcel(grade="Jet A-1", quantity_t=12000, load_port="Rotterdam", discharge_port="Bilbao"),
]


def synth_ray_layout() -> VesselLayout:
    """A fictional 5-pair MR1 with two cargo lines."""
    tanks = []
    for n in range(5):
        no = 5 - n  # COT1 is forward
        a, f = 32 + 20 * n, 52 + 20 * n
        for side in ("P", "S"):
            name = "port" if side == "P" else "starboard"
            tanks.append(LayoutTank(id=f"COT{no}{side}", name=f"Cargo tank {no} {name}", type="cargo", side=side,
                                    xAft=a, xFwd=f, coating="epoxy", lineId="L1" if no <= 2 else "L2"))
            tanks.append(LayoutTank(id=f"WBT{no}{side}", name=f"Water ballast {no} {name}", type="ballast",
                                    side=side, xAft=a, xFwd=f))
    for side in ("P", "S"):
        tanks.append(LayoutTank(id=f"SLOP{side}", name=f"Slop tank {'port' if side == 'P' else 'starboard'}",
                                type="slop", side=side, xAft=28, xFwd=32, coating="epoxy", lineId="L3"))
    tanks.append(LayoutTank(id="FPT", name="Forepeak tank", type="ballast", side="C", xAft=136, xFwd=146))
    return VesselLayout(name="MT Synth Ray", sizeClass="MR1", lbp=150, breadth=27.4, depth=16.5,
                        summerDraught=10.8, tanks=tanks)


SHIP_ID = "mt-synth-ray-demo01"


def seed_memory(user_id: str) -> None:
    """Two example memories, so the Memory page isn't empty for judges."""
    if memory.load(user_id):
        return
    memory.apply_ops(user_id, None, [MemoryOp(op="set", scope="global", kind="grade_alias", key="go 10",
                                              value="ULSD", text="GO 10 means Diesel (ULSD)")], source="demo")
    memory.apply_ops(user_id, SHIP_ID, [MemoryOp(op="set", scope="vessel", kind="max_trim", value=1.5,
                                                 text="Trim at most 1.5 m")], source="demo")


def seed_workspace(user_id: str) -> None:
    if not memory._path(user_id).exists():
        seed_memory(user_id)
    folder = config.USER_DIR / user_id
    if vessels.own_paths(user_id) or any((folder / "plans").glob("*.json")):
        return  # already seeded, or the demo user has made their own data
    layout = synth_ray_layout()
    ship = build_vessel(layout, SHIP_ID)
    ship["layout"] = layout.model_dump(exclude_none=True)
    vessels.save(user_id, ship)

    demo = vessels.get("demo_mr2")
    if demo is not None:
        plan = pipeline._plan_stages(demo, SAMPLE_PARCELS, ConstraintsIn(), None, {}, None)  # stages + cleaning
        pipeline.approve(user_id, {"vessel_id": "demo_mr2", "order_text": SAMPLE_ORDER,
                                   "parcels": [p.model_dump() for p in SAMPLE_PARCELS],
                                   "constraints": ConstraintsIn().model_dump(), "plan": plan}, demo)
