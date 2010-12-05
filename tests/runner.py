'''
Simple test runner

See settings.py file for options&params. Edit as needed.
'''

from subprocess import Popen, PIPE, STDOUT
import os, unittest, tempfile, shutil, time, inspect, sys, math

# Params

abspath = os.path.abspath(os.path.dirname(__file__))
def path_from_root(pathelems):
    return os.path.join(os.path.sep, *(abspath.split(os.sep)[:-1] + pathelems))

EMSCRIPTEN = path_from_root(['emscripten.py'])

exec(open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'settings.py'), 'r').read())

def timeout_run(proc, timeout, note):
  start = time.time()
  if timeout is not None:
    while time.time() - start < timeout and proc.poll() is None:
      time.sleep(0.1)
    if proc.poll() is None:
      proc.kill() # XXX bug: killing emscripten.py does not kill it's child process!
      raise Exception("Timed out: " + note)
  return proc.communicate()[0]

class RunnerCore(unittest.TestCase):
  def get_dir(self):
    dirname = TEMP_DIR + '/tmp' # tempfile.mkdtemp(dir=TEMP_DIR)
    if not os.path.exists(dirname):
      os.makedirs(dirname)
    return dirname

  ## Build JavaScript code from source code
  def build(self, src, dirname, filename, output_processor=None, main_file=None):
    # Copy over necessary files for compiling the source
    if main_file is None:
      f = open(filename, 'w')
      f.write(src)
      f.close()
    else:
      # copy whole directory, and use a specific main .cpp file
      for f in os.listdir(src):
        shutil.copy(os.path.join(src, f), dirname)
      shutil.move(os.path.join(dirname, main_file), filename)

    # Copy Emscripten C++ API
    shutil.copy(path_from_root(['src', 'include', 'emscripten.h']), dirname)

    # C++ => LLVM binary
    try:
      # Make sure we notice if compilation steps failed
      os.remove(filename + '.o')
      os.remove(filename + '.o.ll')
    except:
      pass
    os.chdir(dirname)
    cwd = os.getcwd()
    output = Popen([COMPILER, '-DEMSCRIPTEN', '-emit-llvm'] + COMPILER_OPTS + (['-O3'] if F_OPTS else []) + ['-c', filename, '-o', filename + '.o'], stdout=PIPE, stderr=STDOUT).communicate()[0]
    os.chdir(cwd)
    if not os.path.exists(filename + '.o'):
      print "Failed to compile C/C++ source:\n\n", output
      raise Exception("Compilation error");

    # LLVM binary ==> LLVM assembly
    output = Popen([LLVM_DIS, filename + '.o'] + LLVM_DIS_OPTS + ['-o=' + filename + '.o.ll'], stdout=PIPE, stderr=STDOUT).communicate()[0]

    self.do_emscripten(filename, output_processor)

  def do_emscripten(self, filename, output_processor=None):
    # Run Emscripten
    exported_settings = {}
    for setting in ['QUANTUM_SIZE', 'RELOOP', 'OPTIMIZE', 'GUARD_MEMORY', 'USE_TYPED_ARRAYS', 'SAFE_HEAP']:
      exported_settings[setting] = eval(setting)
    out = open(filename + '.o.js', 'w') if not OUTPUT_TO_SCREEN else None
    timeout_run(Popen([EMSCRIPTEN, filename + '.o.ll', COMPILER_ENGINE[0], str(exported_settings).replace("'", '"')], stdout=out, stderr=STDOUT), TIMEOUT, 'Compiling')
    output = open(filename + '.o.js').read()
    if output_processor is not None:
        output_processor(output)
    if output is not None and 'Traceback' in output: print output; assert 0

  def run_generated_code(self, engine, filename, args=[], check_timeout=True):
    return timeout_run(Popen(engine + [filename] + ([] if engine == SPIDERMONKEY_ENGINE else ['--']) + args,
                       stdout=PIPE, stderr=STDOUT), 120 if check_timeout else None, 'Execution')

  def assertContained(self, value, string):
    if value not in string:
      raise Exception("Expected to find '%s' in '%s'" % (value, string))

  def assertNotContained(self, value, string):
    if value in string:
      raise Exception("Expected to NOT find '%s' in '%s'" % (value, string))

if 'benchmark' not in sys.argv:
  class T(RunnerCore): # Short name, to make it more fun to use manually on the commandline
    ## Does a complete test - builds, runs, checks output, etc.
    def do_test(self, src, expected_output, args=[], output_nicerizer=None, output_processor=None, no_build=False, main_file=None, js_engines=None, post_build=None, basename='src.cpp'):
        print 'Running test:', inspect.stack()[1][3].replace('test_', ''), '[%s,%s,%s]' % (COMPILER.split(os.sep)[-1], 'frontend-optimizations' if F_OPTS else '', 'reloop&optimize' if RELOOP else '')
        dirname = self.get_dir()
        filename = os.path.join(dirname, basename)
        if not no_build:
          self.build(src, dirname, filename, main_file=main_file)

        if post_build is not None:
          post_build(filename + '.o.js')

        # Run in both JavaScript engines, if optimizing - significant differences there (typed arrays)
        if js_engines is None:
          js_engines = [V8_ENGINE] if not OPTIMIZE else [V8_ENGINE, SPIDERMONKEY_ENGINE]
        for engine in js_engines:
          js_output = self.run_generated_code(engine, filename + '.o.js', args)
          if output_nicerizer is not None:
              js_output = output_nicerizer(js_output)
          self.assertContained(expected_output, js_output)
          self.assertNotContained('ERROR', js_output)

        #shutil.rmtree(dirname) # TODO: leave no trace in memory. But for now nice for debugging

    # No building - just process an existing .ll file
    def do_ll_test(self, ll_file, output, args=[], f_opt_ll_file=None, js_engines=None):
      if COMPILER != LLVM_GCC: return # We use existing .ll, so which compiler is unimportant
      if F_OPTS:
        return # TODO: enable the lines below
        #if f_opt_ll_file is None: return # We use existing .ll, so frontend stuff is unimportant, unless we are given an optimized .ll
        #ll_file = f_opt_ll_file

      filename = os.path.join(self.get_dir(), 'src.cpp')
      shutil.copy(ll_file, filename + '.o.ll')
      self.do_emscripten(filename)
      self.do_test(None,
                   output,
                   args,
                   no_build=True,
                   js_engines=js_engines)

    def test_hello_world(self):
        src = '''
          #include <stdio.h>
          int main()
          {
            printf("hello, world!\\n");
            return 0;
          }
        '''
        self.do_test(src, 'hello, world!')

    def test_intvars(self):
        src = '''
          #include <stdio.h>
          int global = 20;
          int *far;
          int main()
          {
            int x = 5;
            int y = x+17;
            int z = (y-1)/2; // Should stay an integer after division!
            y += 1;
            int w = x*3+4;
            int k = w < 15 ? 99 : 101;
            far = &k;
            *far += global;
            int i = k > 100; // Should be an int, not a bool!
            int j = i << 6;
            j >>= 1;
            j = j ^ 5;
            int h = 1;
            h |= 0;
            int p = h;
            p &= 0;
            printf("*%d,%d,%d,%d,%d,%d,%d,%d,%d*\\n", x, y, z, w, k, i, j, h, p);

            long hash = -1;
            size_t perturb;
            int ii = 0;
            for (perturb = hash; ; perturb >>= 5) {
              printf("%d:%d", ii, perturb);
              ii++;
              if (ii == 9) break;
              printf(",");
            }
            printf("*\\n");
            return 0;
          }
        '''
        self.do_test(src, '*5,23,10,19,121,1,37,1,0*\n0:-1,1:134217727,2:4194303,3:131071,4:4095,5:127,6:3,7:0,8:0*')

    def test_unsigned(self):
        src = '''
          #include <stdio.h>
          int main()
          {
            int varey = 100;
            unsigned int MAXEY = -1, MAXEY2 = -77;
            printf("*%u,%d,%u*\\n", MAXEY, varey >= MAXEY, MAXEY2); // 100 >= -1? not in unsigned!
            return 0;
          }
        '''
        self.do_test(src, '*4294967295,0,4294967219*')

    def test_floatvars(self):
        src = '''
          #include <stdio.h>
          int main()
          {
            float x = 1.234, y = 3.5;
            y *= 3;
            int z = x < y;
            printf("*%d,%d,%f,%d,%f\\n", z, int(y), y, (int)x, x);
            return 0;
          }
        '''
        self.do_test(src, '*1,10,10.5,1,1.2339')

    def test_math(self):
        src = '''
          #include <stdio.h>
          #include <cmath>
          int main()
          {
            printf("*%.2f,%.2f,%f*\\n", M_PI, -M_PI, 1/0.0);
            return 0;
          }
        '''
        self.do_test(src, '*3.14,-3.14,Infinity*')

    def test_if(self):
        src = '''
          #include <stdio.h>
          int main()
          {
            int x = 5;
            if (x > 3) {
              printf("*yes*\\n");
            }
            return 0;
          }
        '''
        self.do_test(src, '*yes*')

    def test_if_else(self):
        src = '''
          #include <stdio.h>
          int main()
          {
            int x = 5;
            if (x > 10) {
              printf("*yes*\\n");
            } else {
              printf("*no*\\n");
            }
            return 0;
          }
        '''
        self.do_test(src, '*no*')

    def test_loop(self):
        src = '''
          #include <stdio.h>
          int main()
          {
            int x = 5;
            for (int i = 0; i < 6; i++)
              x += x*i;
            printf("*%d*\\n", x);
            return 0;
          }
        '''
        self.do_test(src, '*3600*')

    def test_stack(self):
        src = '''
          #include <stdio.h>
          int test(int i) {
            int x = 10;
            if (i > 0) {
              return test(i-1);
            }
            return int(&x); // both useful for the number, and forces x to not be nativized
          }
          int main()
          {
            // We should get the same value for the first and last - stack has unwound
            int x1 = test(0);
            int x2 = test(100);
            int x3 = test(0);
            printf("*%d,%d*\\n", x3-x1, x2 != x1);
            return 0;
          }
        '''
        self.do_test(src, '*0,1*')

    def test_strings(self):
        src = '''
          #include <stdio.h>
          #include <stdlib.h>
          #include <string.h>

          int main(int argc, char **argv)
          {
            printf("*%d\\n", argc);
            puts(argv[1]);
            puts(argv[2]);
            printf("%d\\n", atoi(argv[3])+2);
            const char *foolingthecompiler = "\\rabcd";
            printf("%d\\n", strlen(foolingthecompiler)); // Tests parsing /0D in llvm - should not be a 0 (end string) then a D!
            printf("%s\\n", NULL); // Should print '(null)', not the string at address 0, which is a real address for us!
            return 0;
          }
        '''
        self.do_test(src, '*4*wowie*too*76*5*(null)*', ['wowie', 'too', '74'], lambda x: x.replace('\n', '*'))

    def test_funcs(self):
        src = '''
          #include <stdio.h>
          int funcy(int x)
          {
            return x*9;
          }
          int main()
          {
            printf("*%d,%d*\\n", funcy(8), funcy(10));
            return 0;
          }
        '''
        self.do_test(src, '*72,90*')

    def test_structs(self):
        src = '''
          #include <stdio.h>
          struct S
          {
            int x, y;
          };
          int main()
          {
            S a, b;
            a.x = 5; a.y = 6;
            b.x = 101; b.y = 7009;
            S *c, *d;
            c = &a;
            c->x *= 2;
            c = &b;
            c->y -= 1;
            d = c;
            d->y += 10;
            printf("*%d,%d,%d,%d,%d,%d,%d,%d*\\n", a.x, a.y, b.x, b.y, c->x, c->y, d->x, d->y);
            return 0;
          }
        '''
        self.do_test(src, '*10,6,101,7018,101,7018,101,7018*')

    gen_struct_src = '''
          #include <stdio.h>
          #include <stdlib.h>
          #include "emscripten.h"

          struct S
          {
            int x, y;
          };
          int main()
          {
            S* a = {{gen_struct}};
            a->x = 51; a->y = 62;
            printf("*%d,%d*\\n", a->x, a->y);
            {{del_struct}}(a);
            return 0;
          }
    '''

    def test_mallocstruct(self):
        self.do_test(self.gen_struct_src.replace('{{gen_struct}}', '(S*)malloc(ES_SIZEOF(S))').replace('{{del_struct}}', 'free'), '*51,62*')

    def test_newstruct(self):
        self.do_test(self.gen_struct_src.replace('{{gen_struct}}', 'new S').replace('{{del_struct}}', 'delete'), '*51,62*')

    def test_addr_of_stacked(self):
        src = '''
          #include <stdio.h>
          void alter(int *y)
          {
            *y += 5;
          }
          int main()
          {
            int x = 2;
            alter(&x);
            printf("*%d*\\n", x);
            return 0;
          }
        '''
        self.do_test(src, '*7*')

    def test_globals(self):
        src = '''
          #include <stdio.h>

          char cache[256], *next = cache;

          int main()
          {
            cache[10] = 25;
            next[20] = 51;
            printf("*%d,%d*\\n", next[10], cache[20]);
            return 0;
          }
        '''
        self.do_test(src, '*25,51*')

    def test_linked_list(self):
        src = '''
          #include <stdio.h>
          struct worker_args {
            int value;
            struct worker_args *next;
          };
          int main()
          {
            worker_args a;
            worker_args b;
            a.value = 60;
            a.next = &b;
            b.value = 900;
            b.next = NULL;
            worker_args* c = &a;
            int total = 0;
            while (c) {
              total += c->value;
              c = c->next;
            }

            // Chunk of em
            worker_args chunk[10];
            for (int i = 0; i < 9; i++) {
              chunk[i].value = i*10;
              chunk[i].next = &chunk[i+1];
            }
            chunk[9].value = 90;
            chunk[9].next = &chunk[0];

            c = chunk;
            do {
              total += c->value;
              c = c->next;
            } while (c != chunk);
 
            printf("*%d,%d*\\n", total, b.next);
            // NULL *is* 0, in C/C++. No JS null! (null == 0 is false, etc.)

            return 0;
          }
        '''
        self.do_test(src, '*1410,0*')

    def test_assert(self):
        src = '''
          #include <stdio.h>
          #include <assert.h>
          int main() {
            assert(1 == true); // pass
            assert(1 == false); // fail
            return 1;
          }
        '''
        self.do_test(src, 'Assertion failed: 1 == false')

    def test_exceptions(self):
        src = '''
          #include <stdio.h>
          void thrower() {
            printf("infunc...");
            throw(99);
            printf("FAIL");
          }
          int main() {
            try {
              printf("*throw...");
              throw(1);
              printf("FAIL");
            } catch(...) {
              printf("caught!");
            }
            try {
              thrower();
            } catch(...) {
              printf("done!*\\n");
            }
            return 1;
          }
        '''
        self.do_test(src, '*throw...caught!infunc...done!*')

    def test_class(self):
        src = '''
          #include <stdio.h>
          struct Random {
             enum { IM = 139968, IA = 3877, IC = 29573 };
             Random() : last(42) {}
             float get( float max = 1.0f ) {
                last = ( last * IA + IC ) % IM;
                return max * last / IM;
             }
          protected:
             unsigned int last;
          } rng1;
          int main()
          {
            Random rng2;
            int count = 0;
            for (int i = 0; i < 100; i++) {
              float x1 = rng1.get();
              float x2 = rng2.get();
              printf("%f, %f\\n", x1, x2);
              if (x1 != x2) count += 1;
            }
            printf("*%d*\\n", count);
            return 0;
          }
        '''
        self.do_test(src, '*0*')

    def test_inherit(self):
        src = '''
          #include <stdio.h>
          struct Parent {
            int x1, x2;
          };
          struct Child : Parent {
            int y;
          };
          int main()
          {
            Parent a;
            a.x1 = 50;
            a.x2 = 87;
            Child b;
            b.x1 = 78;
            b.x2 = 550;
            b.y = 101;
            Child* c = (Child*)&a;
            c->x1 ++;
            c = &b;
            c->y --;
            printf("*%d,%d,%d,%d,%d,%d,%d*\\n", a.x1, a.x2, b.x1, b.x2, b.y, c->x1, c->x2);
            return 0;
          }
        '''
        self.do_test(src, '*51,87,78,550,100,78,550*')

    def test_polymorph(self):
        src = '''
          #include <stdio.h>
          struct Pure {
            virtual int implme() = 0;
          };
          struct Parent : Pure {
            virtual int getit() { return 11; };
            int implme() { return 32; }
          };
          struct Child : Parent {
            int getit() { return 74; }
            int implme() { return 1012; }
          };

          struct Other {
            int one() { return 11; }
            int two() { return 22; }
          };

          int main()
          {
            Parent *x = new Parent();
            Parent *y = new Child();
            printf("*%d,%d,%d,%d*\\n", x->getit(), y->getit(), x->implme(), y->implme());

            Other *o = new Other;
            int (Other::*Ls)() = &Other::one;
            printf("*%d*\\n", (o->*(Ls))());
            Ls = &Other::two;
            printf("*%d*\\n", (o->*(Ls))());

            return 0;
          }
        '''
        self.do_test(src, '*11,74,32,1012*\n*11*\n*22*')

    def test_funcptr(self):
        src = '''
          #include <stdio.h>
          int calc1() { return 26; }
          int calc2() { return 90; }
          typedef int (*fp_t)();

          fp_t globally1 = calc1;
          fp_t globally2 = calc2;

          int main()
          {
            fp_t fp = calc1;
            void *vp = (void*)fp;
            fp_t fpb = (fp_t)vp;
            fp_t fp2 = calc2;
            void *vp2 = (void*)fp2;
            fp_t fpb2 = (fp_t)vp2;
            printf("*%d,%d,%d,%d,%d,%d*\\n", fp(), fpb(), fp2(), fpb2(), globally1(), globally2());
            return 0;
          }
        '''
        self.do_test(src, '*26,26,90,90,26,90*')

    def test_emptyclass(self):
        src = '''
        #include <stdio.h>

        struct Randomized {
          Randomized(int x) {
            printf("*zzcheezzz*\\n");
          }
        };

        int main( int argc, const char *argv[] ) {
          new Randomized(55);

          return 0;
        }
        '''
        self.do_test(src, '*zzcheezzz*')

    def test_array2(self):
        src = '''
          #include <stdio.h>

          static const double grid[4][2] = {
           {-3/3.,-1/3.},{+1/3.,-3/3.},
           {-1/3.,+3/3.},{+3/3.,+1/3.}
          };

          int main() {
            for (int i = 0; i < 4; i++)
              printf("%d:%.2f,%.2f ", i, grid[i][0], grid[i][1]);
            printf("\\n");
            return 0;
          }
          '''
        self.do_test(src, '0:-1.00,-0.33 1:0.33,-1.00 2:-0.33,1.00 3:1.00,0.33')

    def test_array2b(self):
        src = '''
          #include <stdio.h>

          static const struct {
            unsigned char left;
            unsigned char right;
          } prioritah[] = {
             {6, 6}, {6, 6}, {7, 95}, {7, 7}
          };

          int main() {
            printf("*%d,%d\\n", prioritah[1].left, prioritah[1].right);
            printf("%d,%d*\\n", prioritah[2].left, prioritah[2].right);
            return 0;
          }
          '''
        self.do_test(src, '*6,6\n7,95*')


    def test_constglobalstructs(self):
        src = '''
          #include <stdio.h>
          struct IUB {
             int c;
             double p;
             unsigned int pi;
          };

          IUB iub[] = {
             { 'a', 0.27, 5 },
             { 'c', 0.15, 4 },
             { 'g', 0.12, 3 },
             { 't', 0.27, 2 },
          };

          const unsigned char faceedgesidx[6][4] =
          {
              { 4, 5, 8, 10 },
              { 6, 7, 9, 11 },
              { 0, 2, 8, 9  },
              { 1, 3, 10,11 },
              { 0, 1, 4, 6 },
              { 2, 3, 5, 7 },
          };

          int main( int argc, const char *argv[] ) {
             printf("*%d,%d,%d,%d*\\n", iub[0].c, int(iub[1].p*100), iub[2].pi, faceedgesidx[3][2]);
             return 0;
          }
          '''
        self.do_test(src, '*97,15,3,10*')

    def test_conststructs(self):
        src = '''
          #include <stdio.h>
          struct IUB {
             int c;
             double p;
             unsigned int pi;
          };

          int main( int argc, const char *argv[] ) {
             int before = 70;
             IUB iub[] = {
                { 'a', 0.3029549426680, 5 },
                { 'c', 0.15, 4 },
                { 'g', 0.12, 3 },
                { 't', 0.27, 2 },
             };
             int after = 90;
             printf("*%d,%d,%d,%d,%d,%d*\\n", before, iub[0].c, int(iub[1].p*100), iub[2].pi, int(iub[0].p*10000), after);
             return 0;
          }
          '''
        self.do_test(src, '*70,97,15,3,3029,90*')

    def test_mod_globalstruct(self):
        src = '''
          #include <stdio.h>

          struct malloc_params {
            size_t magic, page_size;
          };

          malloc_params mparams;

          #define SIZE_T_ONE ((size_t)1)
          #define page_align(S) (((S) + (mparams.page_size - SIZE_T_ONE)) & ~(mparams.page_size - SIZE_T_ONE))

          int main()
          {
            mparams.page_size = 4096;
            printf("*%d,%d,%d,%d*\\n", mparams.page_size, page_align(1000), page_align(6000), page_align(66474));
            return 0;
          }
        '''
        self.do_test(src, '*4096,4096,8192,69632*')

    def test_ptrtoint(self):
        src = '''
          #include <stdio.h>

          int main( int argc, const char *argv[] ) {
            char *a = new char[10];
            char *a0 = a+0;
            char *a5 = a+5;
            int *b = new int[10];
            int *b0 = b+0;
            int *b5 = b+5;
            int c = (int)b5-(int)b0; // Emscripten should warn!
            int d = (int)b5-(int)b0; // Emscripten should warn!
            printf("*%d*\\n", (int)a5-(int)a0);
            return 0;
          }
          '''
        runner = self
        def check_warnings(output):
            runner.assertEquals(filter(lambda line: 'Warning' in line, output.split('\n')).__len__(), 4)
        self.do_test(src, '*5*', output_processor=check_warnings)

    def test_sizeof(self):
        # Has invalid writes between printouts
        global SAFE_HEAP; SAFE_HEAP = 0

        src = '''
          #include <stdio.h>
          #include <string.h>
          #include "emscripten.h"

          struct A { int x, y; };

          int main( int argc, const char *argv[] ) {
            int *a = new int[10];
            int *b = new int[1];
            int *c = new int[10];
            for (int i = 0; i < 10; i++)
              a[i] = 2;
            *b = 5;
            for (int i = 0; i < 10; i++)
              c[i] = 8;
            printf("*%d,%d,%d,%d,%d*\\n", a[0], a[9], *b, c[0], c[9]);
            // Should overwrite a, but not touch b!
            memcpy(a, c, 10*ES_SIZEOF(int));
            printf("*%d,%d,%d,%d,%d*\\n", a[0], a[9], *b, c[0], c[9]);

            // Part 2
            A as[3] = { { 5, 12 }, { 6, 990 }, { 7, 2 } };
            memcpy(&as[0], &as[2], ES_SIZEOF(A));

            printf("*%d,%d,%d,%d,%d,%d*\\n", as[0].x, as[0].y, as[1].x, as[1].y, as[2].x, as[2].y);
            return 0;
          }
          '''
        self.do_test(src, '*2,2,5,8,8***8,8,5,8,8***7,2,6,990,7,2*', [], lambda x: x.replace('\n', '*'))

    def test_tinyfuncstr(self):
        src = '''
          #include <stdio.h>

          struct Class {
            static char *name1() { return "nameA"; }
                   char *name2() { return "nameB"; }
          };

          int main() {
            printf("*%s,%s*\\n", Class::name1(), (new Class())->name2());
            return 0;
          }
          '''
        self.do_test(src, '*nameA,nameB*')

    def test_llvmswitch(self):
        src = '''
          #include <stdio.h>
          #include <string.h>

          int switcher(int p)
          {
            switch(p) {
              case 'a':
              case 'b':
              case 'c':
                  return p-1;
              case 'd':
                  return p+1;
            }
            return p;
          }

          int main( int argc, const char *argv[] ) {
            printf("*%d,%d,%d,%d,%d*\\n", switcher('a'), switcher('b'), switcher('c'), switcher('d'), switcher('e'));
            return 0;
          }
          '''
        self.do_test(src, '*96,97,98,101,101*')

    def test_varargs(self):
        src = '''
          #include <stdio.h>
          #include <stdarg.h>

          void vary(const char *s, ...)
          {
            va_list v;
            va_start(v, s);
            char d[20];
            vsnprintf(d, 20, s, v);
            puts(d);

            // Try it with copying
            va_list tempva;
            __va_copy(tempva, v);
            vsnprintf(d, 20, s, tempva);
            puts(d);

            va_end(v);
          }

          void vary2(char color, const char *s, ...)
          {
            va_list v;
            va_start(v, s);
            char d[21];
            d[0] = color;
            vsnprintf(d+1, 20, s, v);
            puts(d);
            va_end(v);
          }

          #define GETMAX(pref, type) \
            type getMax##pref(int num, ...) \
            { \
              va_list vv; \
              va_start(vv, num); \
              type maxx = va_arg(vv, type); \
              for (int i = 1; i < num; i++) \
              { \
                type curr = va_arg(vv, type); \
                maxx = curr > maxx ? curr : maxx; \
              } \
              va_end(vv); \
              return maxx; \
            }
          GETMAX(i, int);
          GETMAX(D, double);

          int main() {
            vary("*cheez: %d+%d*", 0, 24); // Also tests that '0' is not special as an array ender
            vary2('Q', "%d*", 85);

            int maxxi = getMaxi(6,        2, 5, 21, 4, -10, 19);
            printf("maxxi:%d*\\n", maxxi);
            double maxxD = getMaxD(6,        (double)2.1, (double)5.1, (double)22.1, (double)4.1, (double)-10.1, (double)19.1);
            printf("maxxD:%.2f*\\n", (float)maxxD);

            return 0;
          }
          '''
        self.do_test(src, '*cheez: 0+24*\n*cheez: 0+24*\nQ85*\nmaxxi:21*\nmaxxD:22.10*\n')

    def test_stdlibs(self):
        src = '''
          #include <stdio.h>
          #include <stdlib.h>
          #include <sys/time.h>

          void clean()
          {
            printf("*cleaned*\\n");
          }

          int comparer(const void *a, const void *b) {
            int aa = *((int*)a);
            int bb = *((int*)b);
            return aa - bb;
          }

          int main() {
            // timeofday
            timeval t;
            gettimeofday(&t, NULL);
            printf("*%d,%d\\n", int(t.tv_sec), int(t.tv_usec)); // should not crash

            // atexit
            atexit(clean);

            // qsort
            int values[6] = { 3, 2, 5, 1, 5, 6 };
            qsort(values, 5, sizeof(int), comparer);
            printf("*%d,%d,%d,%d,%d,%d*\\n", values[0], values[1], values[2], values[3], values[4], values[5]);
            return 0;
          }
          '''
        self.do_test(src, '*1,2,3,5,5,6*\n*cleaned*')

    def test_statics(self):
        src = '''
          #include <stdio.h>
          #include <string.h>

          #define CONSTRLEN 32

          void conoutfv(const char *fmt)
          {
              static char buf[CONSTRLEN];
              strcpy(buf, fmt);
              puts(buf);
          }

          struct XYZ {
            float x, y, z;
            XYZ(float a, float b, float c) : x(a), y(b), z(c) { }
            static const XYZ& getIdentity()
            {
              static XYZ iT(1,2,3);
              return iT;
            }
          };
          struct S {
            static const XYZ& getIdentity()
            {
              static const XYZ iT(XYZ::getIdentity());
              return iT;
            }
          };

          int main() {
            conoutfv("*staticccz*");
            printf("*%.2f,%.2f,%.2f*\\n", S::getIdentity().x, S::getIdentity().y, S::getIdentity().z);
            return 0;
          }
          '''
        self.do_test(src, '*staticccz*\n*1.00,2.00,3.00*')

    def test_copyop(self):
        # clang generated code is vulnerable to this, as it uses
        # memcpy for assignments, with hardcoded numbers of bytes
        # (llvm-gcc copies items one by one). See QUANTUM_SIZE in
        # settings.js.
        src = '''
          #include <stdio.h>
          #include <math.h>

          struct vec {
            double x,y,z;
            vec() : x(0), y(0), z(0) { };
            vec(const double a, const double b, const double c) : x(a), y(b), z(c) { };
          };

          struct basis {
            vec a, b, c;
            basis(const vec& v) {
              a=v; // should not touch b!
              printf("*%.2f,%.2f,%.2f*\\n", b.x, b.y, b.z);
            }
          };

          int main() {
            basis B(vec(1,0,0));
            return 0;
          }
          '''
        self.do_test(src, '*0.00,0.00,0.00*')

    def test_nestedstructs(self):
        src = '''
          #include <stdio.h>
          #include "emscripten.h"

          struct base {
            int x;
            float y;
            union {
              int a;
              float b;
            };
            char c;
          };

          struct hashtableentry {
            int key;
            base data;
          };

          struct hashset {
            typedef hashtableentry entry;
            struct chain { entry elem; chain *next; };
          //  struct chainchunk { chain chains[100]; chainchunk *next; };
          };

          struct hashtable : hashset {
            hashtable() {
              base *b = NULL;
              entry *e = NULL;
              chain *c = NULL;
              printf("*%d,%d,%d,%d,%d,%d|%d,%d,%d,%d,%d,%d,%d,%d|%d,%d,%d,%d,%d,%d,%d,%d,%d,%d*\\n",
                ES_SIZEOF(base),
                int(&(b->x)), int(&(b->y)), int(&(b->a)), int(&(b->b)), int(&(b->c)), 
                ES_SIZEOF(hashtableentry),
                int(&(e->key)), int(&(e->data)), int(&(e->data.x)), int(&(e->data.y)), int(&(e->data.a)), int(&(e->data.b)), int(&(e->data.c)), 
                ES_SIZEOF(hashset::chain),
                int(&(c->elem)), int(&(c->next)), int(&(c->elem.key)), int(&(c->elem.data)), int(&(c->elem.data.x)), int(&(c->elem.data.y)), int(&(c->elem.data.a)), int(&(c->elem.data.b)), int(&(c->elem.data.c))
              );
            }
          };

          struct B { char buffer[62]; int last; char laster; char laster2; };

          struct Bits {
            unsigned short A : 1;
            unsigned short B : 1;
            unsigned short C : 1;
            unsigned short D : 1;
            unsigned short x1 : 1;
            unsigned short x2 : 1;
            unsigned short x3 : 1;
            unsigned short x4 : 1;
          };

          int main() {
            hashtable t;

            // Part 2 - the char[] should be compressed, BUT have a padding space at the end so the next
            // one is aligned properly. Also handle char; char; etc. properly.
            B *b = NULL;
            printf("*%d,%d,%d,%d,%d,%d,%d,%d,%d*\\n", int(b), int(&(b->buffer)), int(&(b->buffer[0])), int(&(b->buffer[1])), int(&(b->buffer[2])),
                                                      int(&(b->last)), int(&(b->laster)), int(&(b->laster2)), ES_SIZEOF(B));

            // Part 3 - bitfields, and small structures
            Bits *b2 = NULL;
            printf("*%d*\\n", ES_SIZEOF(Bits));

            return 0;
          }
          '''
        if QUANTUM_SIZE == 1:
          # Compressed memory
          self.do_test(src, '*4,0,1,2,2,3|5,0,1,1,2,3,3,4|6,0,5,0,1,1,2,3,3,4*\n*0,0,0,1,2,62,63,64,65*\n*1*')
        else:
          # Bloated memory; same layout as C/C++
          self.do_test(src, '*16,0,4,8,8,12|20,0,4,4,8,12,12,16|24,0,20,0,4,4,8,12,12,16*\n*0,0,0,1,2,64,68,69,72*\n*2*')

    ### 'Big' tests

    def test_fannkuch(self):
        results = [ (1,0), (2,1), (3,2), (4,4), (5,7), (6,10), (7, 16), (8,22) ]
        for i, j in results:
          src = open(path_from_root(['tests', 'fannkuch.cpp']), 'r').read()
          self.do_test(src, 'Pfannkuchen(%d) = %d.' % (i,j), [str(i)], no_build=i>1)

    def test_raytrace(self):
        src = open(path_from_root(['tests', 'raytrace.cpp']), 'r').read()
        output = open(path_from_root(['tests', 'raytrace.ppm']), 'r').read()
        self.do_test(src, output, ['3', '16'])

    def test_dlmalloc(self):
        assert COMPILER_ENGINE != SPIDERMONKEY_ENGINE # See cubescript test
        src = open(path_from_root(['tests', 'dlmalloc.c']), 'r').read()
        self.do_test(src, '*1,0*')

    def test_fasta(self):
        results = [ (1,'''GG*ctt**tgagc*'''), (20,'''GGCCGGGCGCGGTGGCTCACGCCTGTAATCCCAGCACTTT*cttBtatcatatgctaKggNcataaaSatgtaaaDcDRtBggDtctttataattcBgtcg**tacgtgtagcctagtgtttgtgttgcgttatagtctatttgtggacacagtatggtcaaa**tgacgtcttttgatctgacggcgttaacaaagatactctg*'''),
(50,'''GGCCGGGCGCGGTGGCTCACGCCTGTAATCCCAGCACTTTGGGAGGCCGAGGCGGGCGGA*TCACCTGAGGTCAGGAGTTCGAGACCAGCCTGGCCAACAT*cttBtatcatatgctaKggNcataaaSatgtaaaDcDRtBggDtctttataattcBgtcg**tactDtDagcctatttSVHtHttKtgtHMaSattgWaHKHttttagacatWatgtRgaaa**NtactMcSMtYtcMgRtacttctWBacgaa**agatactctgggcaacacacatacttctctcatgttgtttcttcggacctttcataacct**ttcctggcacatggttagctgcacatcacaggattgtaagggtctagtggttcagtgagc**ggaatatcattcgtcggtggtgttaatctatctcggtgtagcttataaatgcatccgtaa**gaatattatgtttatttgtcggtacgttcatggtagtggtgtcgccgatttagacgtaaa**ggcatgtatg*''') ]
        for i, j in results:
          src = open(path_from_root(['tests', 'fasta.cpp']), 'r').read()
          self.do_test(src, j, [str(i)], lambda x: x.replace('\n', '*'), no_build=i>1)

    def test_cubescript(self):
      # XXX Warning: Running this in SpiderMonkey can lead to an extreme amount of memory being
      #              used, see Mozilla bug 593659.
      assert COMPILER_ENGINE != SPIDERMONKEY_ENGINE

      global SAFE_HEAP; SAFE_HEAP = 0 # Has some actual loads of unwritten-to places, in the C++ code...

      self.do_test(path_from_root(['tests', 'cubescript']), '*\nTemp is 33\n9\n5\nhello, everyone\n*', main_file='command.cpp')

    def test_gcc_unmangler(self):
      if F_OPTS: return # See issue #8 - frontend optimizations fail here
      self.do_test(path_from_root(['third_party']), '*d_demangle(char const*, int, unsigned int*)*', args=['_ZL10d_demanglePKciPj'], main_file='gcc_demangler.c')

    def test_bullet(self):
      global SAFE_HEAP; SAFE_HEAP = 0 # Too slow for that
      self.do_ll_test(path_from_root(['tests', 'bullet', 'bulletTest.ll']), open(path_from_root(['tests', 'bullet', 'output.txt']), 'r').read())

    def test_lua(self):
      self.do_ll_test(path_from_root(['tests', 'lua', 'lua.ll']),
                      'hello lua world!\n\n\n17.00000000000\n\n\n1.00000000000\n\n\n2.00000000000\n\n\n3.00000000000\n\n\n4.00000000000\n\n\n7.00000000000',
                      args=['-e', '''print("hello lua world!");print(17);for x = 1,4 do print(x) end;print(10-3)'''],
                      f_opt_ll_file=path_from_root(['tests', 'lua', 'lua.Os.ll']))

    ### Test cases in separate files

    def test_cases(self):
      for name in os.listdir(path_from_root(['tests', 'cases'])):
        print "Testing case '%s'..." % name.replace('.ll', '')
        self.do_ll_test(path_from_root(['tests', 'cases', name]), 'hello, world!')

    ### Integration tests

    def test_scriptaclass(self):
        src = '''
          struct ScriptMe {
            int value;
            ScriptMe(int val);
            int getVal(); // XXX Sadly, inlining these will result in LLVM not
                          // producing any code for them (when just building
                          // as a library)
            void mulVal(int mul);
          };
          ScriptMe::ScriptMe(int val) : value(val) { }
          int ScriptMe::getVal() { return value; }
          void ScriptMe::mulVal(int mul) { value *= mul; }
        '''
        script_src = '''
          var sme = Module._.ScriptMe.__new__(83);          // malloc(sizeof(ScriptMe)), ScriptMe::ScriptMe(sme, 83) / new ScriptMe(83) (at addr sme)
          Module._.ScriptMe.mulVal(sme, 2);                 // ScriptMe::mulVal(sme, 2)       sme.mulVal(2)
          print('*' + Module._.ScriptMe.getVal(sme) + '*'); 
          Module._free(sme);
          print('*ok*');
        '''
        def post(filename):
          Popen(['python', DEMANGLER, filename, '.'], stdout=open(filename + '.tmp', 'w')).communicate()
          Popen(['python', NAMESPACER, filename + '.tmp'], stdout=open(filename + '.tmp2', 'w')).communicate()
          src = open(filename, 'r').read().replace(
            '// {{MODULE_ADDITIONS}',
            'Module["_"] = ' + open(filename + '.tmp2', 'r').read().rstrip() + ';\n\n' + script_src + '\n\n' +
              '// {{MODULE_ADDITIONS}'
          )

          open(filename, 'w').write(src)
        self.do_test(src, '*166*\n*ok*', post_build=post)

    ### Tests for tools

    def test_safe_heap(self):
      if not SAFE_HEAP: return
      if F_OPTS: return
      src = '''
        #include<stdio.h>
        int main() {
          int *x = new int;
          *x = 20;
          float *y = (float*)x;
          printf("%f\\n", *y);
          return 0;
        }
      '''
      try:
        self.do_test(src, '*nothingatall*')
      except Exception, e:
        assert 'Assertion failed: Load-store consistency assumption failure!' in str(e), str(e)

  # Generate tests for all our compilers
  def make_test(compiler, f_opts, embetter):
    class TT(T):
      def setUp(self):
        global COMPILER, QUANTUM_SIZE, RELOOP, OPTIMIZE, GUARD_MEMORY, USE_TYPED_ARRAYS, F_OPTS, SAFE_HEAP
        COMPILER = compiler['path']
        QUANTUM_SIZE = compiler['quantum_size']
        RELOOP = OPTIMIZE = USE_TYPED_ARRAYS = embetter
        GUARD_MEMORY = SAFE_HEAP = 1-embetter
        F_OPTS = f_opts
    return TT
  for embetter in [0,1]:
    for f_opts in [0,1]:
      for name in COMPILERS.keys():
        exec('T_%s_%d_%d = make_test(COMPILERS["%s"],%d,%d)' % (name, f_opts, embetter, name, f_opts, embetter))
  del T # T is just a shape for the specific subclasses, we don't test it itself

else:
  # Benchmarks

  sys.argv = filter(lambda x: x != 'benchmark', sys.argv)

  assert(os.path.exists(CLOSURE_COMPILER))

  COMPILER = LLVM_GCC
  JS_ENGINE = SPIDERMONKEY_ENGINE
  #JS_ENGINE = V8_ENGINE

  QUANTUM_SIZE = 4
  RELOOP = OPTIMIZE = USE_TYPED_ARRAYS = 1
  GUARD_MEMORY = SAFE_HEAP = 0
  F_OPTS = 1

  TEST_REPS = 10
  TOTAL_TESTS = 2

  tests_done = 0
  total_times = map(lambda x: 0., range(TEST_REPS))

  class Benchmark(RunnerCore):
    def print_stats(self, times):
      mean = sum(times)/len(times)
      squared_times = map(lambda x: x*x, times)
      mean_of_squared = sum(squared_times)/len(times)
      std = math.sqrt(mean_of_squared - mean*mean)
      print '   mean: %.3f (+-%.3f) seconds          (max: %.3f, min: %.3f, noise/signal: %.3f)     (%d runs)' % (mean, std, max(times), min(times), std/mean, TEST_REPS)

    def do_benchmark(self, src, args=[], expected_output='FAIL', main_file=None):
      print 'Running benchmark:', inspect.stack()[1][3].replace('test_', '')

      dirname = self.get_dir()
      filename = os.path.join(dirname, 'src.cpp')
      self.build(src, dirname, filename, main_file=main_file)

      # Optimize using closure compiler
      try:
        os.remove(filename + '.cc.js')
      except:
        pass
      # Something like this:
      #   java -jar CLOSURE_COMPILER --compilation_level ADVANCED_OPTIMIZATIONS --formatting PRETTY_PRINT --variable_map_output_file src.cpp.o.js.vars --js src.cpp.o.js --js_output_file src.cpp.o.cc.js

      cc_output = Popen(['java', '-jar', CLOSURE_COMPILER,
                         '--compilation_level', 'ADVANCED_OPTIMIZATIONS',
                         '--formatting', 'PRETTY_PRINT',
                         '--variable_map_output_file', filename + '.vars',
                         '--js', filename + '.o.js', '--js_output_file', filename + '.cc.js'], stdout=PIPE, stderr=STDOUT).communicate()[0]
      if 'ERROR' in cc_output:
        raise Exception('Error in cc output: ' + cc_output)

      # Run
      global total_times
      times = []
      for i in range(TEST_REPS):
        start = time.time()
        js_output = self.run_generated_code(JS_ENGINE, filename + '.cc.js', args, check_timeout=False)
        curr = time.time()-start
        times.append(curr)
        total_times[i] += curr
        if i == 0:
          # Sanity check on output
          self.assertContained(expected_output, js_output)

      self.print_stats(times)

      global tests_done
      tests_done += 1
      if tests_done == TOTAL_TESTS:
        print
        print 'Total stats:'
        self.print_stats(total_times)

    def test_fannkuch(self):
      src = open(path_from_root(['tests', 'fannkuch.cpp']), 'r').read()
      self.do_benchmark(src, ['9'], 'Pfannkuchen(9) = 30.')

    def test_raytrace(self):
      src = open(path_from_root(['tests', 'raytrace.cpp']), 'r').read()
      self.do_benchmark(src, ['5', '64'], open(path_from_root(['tests', 'raytrace_5_64.ppm']), 'r').read())

if __name__ == '__main__':
  for cmd in map(lambda compiler: compiler['path'], COMPILERS.values()) + [LLVM_DIS, SPIDERMONKEY_ENGINE[0], V8_ENGINE[0]]:
    print "Checking for existence of", cmd
    assert(os.path.exists(cmd))
  print "Running Emscripten tests..."
  print '', # indent so when next lines have '.', they all align
  unittest.main()

