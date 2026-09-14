import { Nav } from "@/components/Nav";

// como-funciona/page.tsx — página nueva, sin datos: explica el motor paso a
// paso para alguien que abre el dashboard sin haber visto el proyecto antes.
// No sustituye a la documentación técnica (docs/ARCHITECTURE_LEAN.md) — es
// la versión de un párrafo por concepto, en español llano, pensada para
// leerse en 3 minutos.

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4">
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 flex items-center justify-center text-sm font-bold">
        {n}
      </div>
      <div className="pb-6 border-l border-neutral-200 dark:border-neutral-800 pl-4 -ml-4 mt-1">
        <p className="font-semibold mb-1">{title}</p>
        <div className="text-sm text-neutral-600 dark:text-neutral-400 space-y-2">{children}</div>
      </div>
    </div>
  );
}

export default function ComoFuncionaPage() {
  return (
    <main className="max-w-3xl mx-auto p-6">
      <Nav active="/como-funciona" />
      <header className="mb-8">
        <h1 className="text-2xl font-bold">Cómo funciona</h1>
        <p className="text-sm text-neutral-500 mt-1">Qué hace el sistema, paso a paso, sin dar nada por sabido.</p>
      </header>

      <section className="mb-8">
        <p className="text-sm mb-4">
          Cada noche, el sistema recorre este proceso para cada empresa que presenta un documento oficial ante la SEC (el regulador de
          bolsa de EE.UU.) o recibe una decisión de la FDA (el regulador de medicamentos). El objetivo NO es adivinar el futuro — es
          detectar cuándo una noticia real todavía no se ha reflejado del todo en el precio, y medir con cuánta confianza se puede decir
          eso.
        </p>
      </section>

      <section>
        <Step n={1} title="Detectar el evento">
          <p>
            Cada noche se revisan los documentos oficiales publicados el día anterior (resultados trimestrales, cambios de dirección,
            contratos importantes, decisiones de la FDA...). Se clasifican por tipo automáticamente.
          </p>
        </Step>

        <Step n={2} title="¿Es esto una sorpresa? (Novelty)">
          <p>
            Si la empresa ya había avisado de esto antes, o el precio ya se movió en los días previos anticipándolo, la "sorpresa" es
            baja — y algo que el mercado ya sabe no da ventaja. Se mide qué tan nuevo es realmente el evento.
          </p>
        </Step>

        <Step n={3} title="El debate: Bull vs Bear vs Juez">
          <p>
            Un modelo de IA construye la mejor tesis <strong>a favor</strong> (Bull) y otro la mejor tesis <strong>en contra</strong>{" "}
            (Bear) del evento — cada uno defendiendo su lado con los hechos disponibles, sin inventar cifras. Un tercer modelo (el Juez)
            evalúa el debate y decide qué lado convence más y con cuánta seguridad.
          </p>
        </Step>

        <Step n={4} title="¿Qué pasó otras veces con eventos parecidos?">
          <p>
            Se buscan eventos históricos del mismo tipo (mismo tipo de anuncio, en el pasado) y se mide cuánto se movió el precio en esos
            casos. Cuantos menos casos históricos parecidos haya, menos se confía en la magnitud estimada — con pocos ejemplos, la
            estimación se acerca a la media general de esa categoría en vez de fiarse de una muestra pequeña.
          </p>
        </Step>

        <Step n={5} title="Valor esperado (EV)">
          <p>
            Se combina la dirección y fuerza del veredicto del Juez con la magnitud típica histórica, para estimar cuánto se podría ganar
            o perder, en promedio, operando esta situación.
          </p>
        </Step>

        <Step n={6} title="¿Merece la pena operar, o mejor abstenerse?">
          <p>
            Antes de decidir "operar", el sistema comprueba 7 condiciones: ¿es realmente una sorpresa?, ¿el Juez tiene suficiente
            seguridad?, ¿el valor esperado compensa las comisiones?, ¿hay señales de que el dato es poco fiable?, ¿la acción tiene
            suficiente liquidez?... Si CUALQUIERA falla, la decisión es <strong>no operar</strong> — abstenerse es un resultado válido,
            no un fallo del sistema.
          </p>
        </Step>

        <Step n={7} title="Simulación (nunca dinero real)">
          <p>
            Cuando sí se decide operar, se simula la operación con reglas realistas (comisiones, entrada al día siguiente, stop-loss,
            take-profit) para ver qué habría pasado. Esto se hace de dos formas: sobre todo el histórico (<strong>Cartera</strong>) y en
            vivo esta semana con datos reales pero sin arriesgar dinero (<strong>papel</strong>).
          </p>
        </Step>

        <Step n={8} title="¿Realmente funciona esto?">
          <p>
            Por último, se audita el propio sistema: ¿el tipo de evento mueve el precio de forma estadísticamente real, o podría ser
            ruido? ¿El sistema sabe cuándo confiar en sí mismo (si dice "80% seguro", acierta de verdad el 80% de las veces)? ¿El
            resultado se mantiene si suben las comisiones o cambia el mercado? Todo esto está en la pestaña{" "}
            <strong>¿Funciona?</strong>
          </p>
        </Step>
      </section>

      <section className="mt-8 border-t border-neutral-200 dark:border-neutral-800 pt-6">
        <p className="font-semibold mb-2">Reglas que el sistema nunca rompe</p>
        <ul className="text-sm text-neutral-600 dark:text-neutral-400 list-disc list-inside space-y-1">
          <li>Nunca usa información que no existía en el momento de la decisión (nada de "trampa" mirando al futuro).</li>
          <li>Nunca inventa un número que no pueda calcular — si un dato no está disponible, lo dice, no lo estima a ciegas.</li>
          <li>Prefiere decir "no operar" antes que fingir seguridad que no tiene.</li>
        </ul>
      </section>
    </main>
  );
}
