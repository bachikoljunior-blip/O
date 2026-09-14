# User design context: Skill-in-Skill

The original intended meaning of Skill-in-Skill is hierarchical, situation-dependent context routing.

A Skill may contain or reference multiple child Skills. At each level, the current situation determines which relevant child Skill or Skills to open; those children may themselves expose multiple child Skills, and selection can continue recursively for as many levels as are useful. Only the context needed along the selected branches should be materialized for the current reasoning step rather than loading the whole Skill structure.

This is not limited to a fixed-depth chain or to multi-Skill functional composition; the core intent is recursive selection of needed context from a potentially deep Skill hierarchy.