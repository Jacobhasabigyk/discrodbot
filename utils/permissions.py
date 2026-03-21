def has_role_interaction(interaction, roles):
    user_roles = [role.id for role in interaction.user.roles]
    return any(role in user_roles for role in roles)