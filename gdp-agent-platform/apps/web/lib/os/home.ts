/** Where a signed-in person lands: Agentic Data OS mission control when ADOS_LANDING=1, else the classic My Work. */
export const homePath = () => (process.env.ADOS_LANDING === "1" ? "/os" : "/os/work/inbox");
